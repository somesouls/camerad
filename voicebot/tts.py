# -*- coding: utf-8 -*-
"""voicebot/tts.py -- TTS LOKAL: Piper (default, ringan) atau MMS-TTS (natural).

Dua mesin, dipilih lewat setting `tts_engine`:
  - 'piper' (default): binary Piper standalone, sangat ringan, jalan di CPU.
  - 'mms'  : facebook/mms-tts-ind (Meta, VITS) via transformers+torch. NATIVE
             Bahasa Indonesia dan lebih natural dari Piper. Butuh unduhan model
             sekali dari HuggingFace (~145 MB), setelah itu jalan penuh lokal;
             bisa GPU (RTX 5060 Ti) atau CPU.

Piper env:
  VOICEBOT_PIPER_BIN   -- path/nama binary piper (default 'piper'). Di WINDOWS
                          pakai piper.exe STANDALONE (rilis resmi), BUKAN paket
                          pip 'piper-tts' yang sering gagal (piper-phonemize).
  VOICEBOT_PIPER_VOICE -- path model suara .onnx id-ID (WAJIB untuk Piper).
                          File <voice>.onnx.json harus ada di folder yang sama.
  VOICEBOT_PIPER_ESPEAK_DATA -- (opsional) path folder espeak-ng-data.

MMS env (opsional):
  VOICEBOT_MMS_MODEL   -- override id model (default dari setting `mms_model`
                          atau 'facebook/mms-tts-ind').
  VOICEBOT_MMS_DEVICE  -- 'cuda' | 'cpu' (default: auto — cuda bila tersedia).

Sebelum sintesis, teks dilewatkan lapisan pelafalan (voicebot.pron): kamus
singkatan + eja angka panjang. Bisa dimatikan via setting pron_enabled.

Fail-soft berlapis: bila mesin terpilih belum siap, synth() -> (None, alasan)
sehingga engine tetap menjawab dalam bentuk teks. Bila MMS gagal saat runtime
namun Piper siap, otomatis jatuh ke Piper agar tetap ada suara.
"""
import os
import io
import wave
import shutil
import struct
import subprocess
import tempfile


# ------------------------------------------------------------------ util config
def _engine():
    """Nama mesin TTS terpilih: 'piper' (default) | 'mms'. Fail-soft."""
    try:
        from voicebot import config_db as _cfg
        return (_cfg.get_setting("tts_engine", "piper") or "piper").strip().lower()
    except Exception:
        return "piper"


def _bin():
    return os.environ.get("VOICEBOT_PIPER_BIN") or "piper"


def _voice():
    return os.environ.get("VOICEBOT_PIPER_VOICE") or ""


def _resolve_bin():
    """Kembalikan path binary piper yang bisa dijalankan, atau None."""
    b = _bin()
    if os.path.exists(b):
        return b
    return shutil.which(b)


def _mms_model_id():
    env = os.environ.get("VOICEBOT_MMS_MODEL")
    if env:
        return env
    try:
        from voicebot import config_db as _cfg
        return (_cfg.get_setting("mms_model", "facebook/mms-tts-ind")
                or "facebook/mms-tts-ind")
    except Exception:
        return "facebook/mms-tts-ind"


def _mms_deps_present():
    """Cek ringan (tanpa impor berat) apakah transformers+torch terpasang."""
    try:
        import importlib.util as _u
        return bool(_u.find_spec("transformers") and _u.find_spec("torch"))
    except Exception:
        return False


# ------------------------------------------------------------------ ketersediaan
def piper_available():
    if not _voice():
        return False
    return _resolve_bin() is not None


def mms_available():
    return _mms_deps_present()


def available():
    """Ketersediaan mesin TTS yang SEDANG dipilih."""
    if _engine() == "mms":
        return mms_available()
    return piper_available()


def diagnostics():
    """Info status TTS untuk halaman konfigurasi / health."""
    voice = _voice()
    eng = _engine()
    return {
        "engine": eng,
        "ready": available(),
        # Piper
        "bin": _bin(),
        "bin_resolved": _resolve_bin(),
        "voice": voice,
        "voice_exists": bool(voice and os.path.exists(voice)),
        "config_exists": bool(voice and os.path.exists(voice + ".json")),
        "piper_ready": piper_available(),
        # MMS
        "mms_model": _mms_model_id(),
        "mms_deps": _mms_deps_present(),
        "mms_ready": mms_available(),
    }


def _pronounce(text):
    """Terapkan lapisan pelafalan (kamus + angka) bila diaktifkan. Fail-soft."""
    try:
        from voicebot import config_db as _cfg
        if str(_cfg.get_setting("pron_enabled", "1")) == "0":
            return text
        from voicebot import pron as _pron
        return _pron.normalize(text)
    except Exception:  # noqa: BLE001
        return text


# ------------------------------------------------------------------ Piper
def _synth_piper(text):
    """Sintesis via Piper. Kembalikan (wav_bytes, error)."""
    voice = _voice()
    if not voice:
        return None, ("Piper belum dikonfigurasi "
                      "(set VOICEBOT_PIPER_VOICE ke file .onnx).")
    binpath = _resolve_bin()
    if not binpath:
        return None, ("Binary piper tidak ditemukan "
                      "(set VOICEBOT_PIPER_BIN ke path piper.exe / piper).")
    if not os.path.exists(voice):
        return None, "Model suara tidak ditemukan: %s" % voice
    if not os.path.exists(voice + ".json"):
        return None, ("Config model tidak ditemukan: %s.json "
                      "(letakkan file .onnx.json di folder yang sama)." % voice)

    out = tempfile.NamedTemporaryFile(prefix="vb_tts_", suffix=".wav",
                                      delete=False)
    out.close()
    try:
        cmd = [binpath, "-m", voice, "-f", out.name]
        espeak = os.environ.get("VOICEBOT_PIPER_ESPEAK_DATA")
        if espeak:
            cmd += ["--espeak_data", espeak]
        p = subprocess.run(cmd, input=text.encode("utf-8"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=120)
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "ignore").strip()
            tail = err.splitlines()[-1] if err else "(tanpa pesan)"
            return None, "piper gagal (exit %s): %s" % (p.returncode, tail[:400])
        with open(out.name, "rb") as f:
            data = f.read()
        if not data:
            return None, "piper tidak menghasilkan audio (output kosong)."
        return data, None
    except FileNotFoundError:
        return None, "Binary piper tidak bisa dijalankan: %s" % binpath
    except subprocess.TimeoutExpired:
        return None, "piper timeout (>120 dtk)."
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    finally:
        try:
            os.unlink(out.name)
        except Exception:
            pass


# ------------------------------------------------------------------ MMS-TTS
_MMS = {}  # cache: {model_id: (model, tokenizer, device, sample_rate)}


def _mms_load():
    """Muat (sekali) model MMS-TTS + tokenizer. Kembalikan (obj, error)."""
    mid = _mms_model_id()
    if mid in _MMS:
        return _MMS[mid], None
    if not _mms_deps_present():
        return None, ("Dependensi MMS belum terpasang. Install: "
                      "pip install transformers torch")
    try:
        import torch
        from transformers import VitsModel, AutoTokenizer
        model = VitsModel.from_pretrained(mid)
        tok = AutoTokenizer.from_pretrained(mid)
        dev = os.environ.get("VOICEBOT_MMS_DEVICE")
        if not dev:
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = model.to(dev)
        except Exception:
            dev = "cpu"
            model = model.to(dev)
        model.eval()
        sr = int(getattr(model.config, "sampling_rate", 16000) or 16000)
        _MMS[mid] = (model, tok, dev, sr)
        return _MMS[mid], None
    except Exception as e:  # noqa: BLE001
        return None, "gagal memuat model MMS '%s': %s" % (mid, e)


def _pcm16_wav(samples, sample_rate):
    """Bungkus list/array float [-1,1] jadi WAV PCM16 (bytes)."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(int(sample_rate))
    frames = bytearray()
    for v in samples:
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        frames += struct.pack("<h", int(v * 32767.0))
    w.writeframes(bytes(frames))
    w.close()
    return buf.getvalue()


def _synth_mms(text):
    """Sintesis via MMS-TTS (facebook/mms-tts-ind). Kembalikan (wav_bytes, error)."""
    obj, err = _mms_load()
    if not obj:
        return None, err
    model, tok, dev, sr = obj
    try:
        import torch
        inputs = tok(text, return_tensors="pt")
        input_ids = inputs["input_ids"]
        try:
            input_ids = input_ids.to(dev)
        except Exception:
            pass
        with torch.no_grad():
            output = model(input_ids=input_ids).waveform
        wav = output.squeeze().detach().to("cpu").tolist()
        if isinstance(wav, float):
            wav = [wav]
        if not wav:
            return None, "MMS tidak menghasilkan audio (output kosong)."
        return _pcm16_wav(wav, sr), None
    except Exception as e:  # noqa: BLE001
        return None, "MMS gagal saat sintesis: %s" % e


# ------------------------------------------------------------------ API utama
def synth(text):
    """Kembalikan (wav_bytes, error). wav_bytes None bila gagal/tak tersedia.

    Mesin ditentukan setting `tts_engine`. Bila 'mms' gagal saat runtime tetapi
    Piper siap, otomatis jatuh ke Piper (fail-soft) agar tetap ada suara.
    """
    text = (text or "").strip()
    if not text:
        return None, "teks kosong"

    # lapisan pelafalan sebelum TTS (tidak mengubah teks yang ditampilkan ke klien)
    text = _pronounce(text)

    if _engine() == "mms":
        data, err = _synth_mms(text)
        if data:
            return data, None
        # cadangan: coba Piper bila terkonfigurasi
        if piper_available():
            print("[voicebot.tts] MMS gagal (%s); memakai Piper." % err, flush=True)
            data2, err2 = _synth_piper(text)
            if data2:
                return data2, None
            return None, "MMS gagal: %s | Piper gagal: %s" % (err, err2)
        return None, err

    return _synth_piper(text)
