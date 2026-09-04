# -*- coding: utf-8 -*-
"""voicebot/tts.py -- TTS LOKAL: Piper (default, ringan) atau MMS-TTS (natural).

Dua mesin, dipilih lewat setting `tts_engine`:
  - 'piper' (default): binary Piper standalone, sangat ringan, jalan di CPU.
  - 'mms'  : facebook/mms-tts-ind (Meta, VITS) via transformers+torch. NATIVE
             Bahasa Indonesia dan lebih natural dari Piper. Butuh unduhan model
             sekali dari HuggingFace (~145 MB), setelah itu jalan penuh lokal;
             bisa GPU (RTX 5060 Ti) atau CPU.

CACHE SINTESIS (latency): synth() menyimpan hasil WAV di memori proses dengan
kunci (mesin + suara/model + teks tersanitasi). Frasa yang SERING BERULANG --
salam pembuka, kalimat konfirmasi, sapaan penjaga diam, filler -- tak perlu
disintesis ulang sehingga giliran berikutnya tidak menanggung ~3 dtk TTS.
Dikontrol setting `tts_cache_enabled` (default '1') & `tts_cache_max` (default
64 entri, LRU). Hanya hasil SUKSES yang disimpan. clear_cache() dipanggil saat
konfigurasi/suara berubah. Semua fail-soft.

KEANDALAN SUARA (penting): synth() dirancang supaya suara SELALU diusahakan keluar.
  - Piper gagal transien (subprocess/output kosong) -> otomatis DICOBA ULANG 1x
    dengan MESIN YANG SAMA (tidak mengganti suara).
  - Cadangan LINTAS-MESIN (piper<->mms) kini OPSIONAL dan MATI secara default
    (setting `tts_cross_fallback`, default '0'). Sesuai permintaan: bila memilih
    Piper, suara yang keluar HANYA Piper -- tidak dicampur MMS supaya suara tidak
    berganti-ganti / tumpang tindih. Aktifkan hanya bila memang ingin fail-over.
  - Setiap kegagalan DICATAT ke log server ([voicebot.tts] ...) lengkap dengan
    alasan + panjang teks, supaya turn yang "gagal TTS" bisa didiagnosis.

SANITASI TEKS (penting untuk Piper): sebelum sintesis, teks dibersihkan lewat
_sanitize_for_tts() supaya AMAN dan JELAS diucapkan:
  - BUANG surrogate liar (mis. '\udc9d'). Ini akar 'UnicodeEncodeError:
    surrogates not allowed' -- Python gagal encode input SEBELUM Piper sempat
    memproses, lalu Piper exit 1. Lokasi surrogate dicatat (index/repr/code)
    untuk diagnosa.
  - BUANG emoji & simbol piktografik (mis. thumbs/panah/keycap 0-9), variation
    selector, zero-width, dsb -> supaya suara tak membaca 'emoji' dan hanya
    berupa teks yang jelas.
  - Normalkan tanda baca tipografis (kutip lengkung, dash panjang, elipsis) ke
    ASCII, dan ubah underscore nama intent ('Administrasi_EFIN_...') jadi spasi.
  Tanda baca kalimat biasa (.,!?;:-()\"') DIPERTAHANKAN karena membantu intonasi.

CATATAN PIPER (penting): paket pip 'piper-tts' menulis WAV lewat modul wave
Python dan pada sebagian versi gagal dengan 'wave.Error: # channels not
specified' saat memakai output file (-f). Karena itu Piper dipanggil dengan
--output-raw (PCM16 mono ke stdout) lalu WAV dibungkus sendiri di sini; mode
file (-f) hanya dipakai sebagai cadangan bila varian raw tak didukung.

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
  VOICEBOT_MMS_DEVICE  -- 'cuda' | 'cpu' (default: auto -- cuda bila tersedia).

Sebelum sintesis, teks dilewatkan lapisan pelafalan (voicebot.pron): kamus
singkatan + eja angka panjang. Bisa dimatikan via setting pron_enabled.

Fail-soft berlapis: bila mesin terpilih gagal (dan cadangan mati/juga gagal),
synth() -> (None, alasan) sehingga engine tetap menjawab dalam bentuk teks.
"""
import os
import io
import re
import json
import wave
import shutil
import struct
import subprocess
import tempfile
from collections import OrderedDict


def _log(msg):
    """Log ringkas ke stdout server (fail-soft)."""
    try:
        print("[voicebot.tts] " + msg, flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ util config
def _engine():
    """Nama mesin TTS terpilih: 'piper' (default) | 'mms'. Fail-soft."""
    try:
        from voicebot import config_db as _cfg
        return (_cfg.get_setting("tts_engine", "piper") or "piper").strip().lower()
    except Exception:
        return "piper"


def _cross_fallback_on():
    """Boleh jatuh ke mesin TTS lain bila mesin terpilih gagal? Default: TIDAK.

    Dikontrol setting `tts_cross_fallback` (default '0' = mati). Saat mati, mesin
    yang dipilih di Konfigurasi dipakai apa adanya -- suara tidak berganti mesin.
    """
    off = ("0", "false", "False", "no", "NO", "")
    try:
        from voicebot import config_db as _cfg
        return str(_cfg.get_setting("tts_cross_fallback", "0")) not in off
    except Exception:
        return False


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


# ------------------------------------------------------------------ cache
# Cache hasil sintesis di memori proses. Kunci = mesin + identitas suara/model +
# teks TERSANITASI. Frasa berulang (salam/konfirmasi/penjaga diam/filler) tak
# perlu disintesis ulang -> hemat ~3 dtk per giliran. LRU sederhana, hanya hasil
# sukses yang disimpan. Semua fail-soft.
_SYNTH_CACHE = OrderedDict()


def _cache_enabled():
    off = ("0", "false", "False", "no", "NO", "")
    try:
        from voicebot import config_db as _cfg
        return str(_cfg.get_setting("tts_cache_enabled", "1")) not in off
    except Exception:
        return True


def _cache_max():
    try:
        from voicebot import config_db as _cfg
        n = int(_cfg.get_setting("tts_cache_max", "64") or 64)
        return n if n > 0 else 64
    except Exception:
        return 64


def _cache_identity():
    """Identitas mesin utk kunci cache (suara Piper / model MMS)."""
    if _engine() == "mms":
        return "mms:" + _mms_model_id()
    return "piper:" + (_voice() or "")


def _cache_get(text):
    if not _cache_enabled():
        return None
    try:
        key = _engine() + "|" + _cache_identity() + "|" + text
        wav = _SYNTH_CACHE.get(key)
        if wav is not None:
            _SYNTH_CACHE.move_to_end(key)
            _log("cache HIT (%d frasa tersimpan, %d byte)."
                 % (len(_SYNTH_CACHE), len(wav)))
        return wav
    except Exception:
        return None


def _cache_put(text, wav):
    if not wav or not _cache_enabled():
        return
    try:
        key = _engine() + "|" + _cache_identity() + "|" + text
        _SYNTH_CACHE[key] = wav
        _SYNTH_CACHE.move_to_end(key)
        while len(_SYNTH_CACHE) > _cache_max():
            _SYNTH_CACHE.popitem(last=False)
    except Exception:
        pass


def clear_cache():
    """Kosongkan cache TTS (mis. saat suara/model/konfigurasi berubah). Fail-soft."""
    try:
        _SYNTH_CACHE.clear()
        _log("cache dikosongkan.")
    except Exception:
        pass


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
        "cross_fallback": _cross_fallback_on(),
        "cache_enabled": _cache_enabled(),
        "cache_size": len(_SYNTH_CACHE),
        # Piper
        "bin": _bin(),
        "bin_resolved": _resolve_bin(),
        "voice": voice,
        "voice_exists": bool(voice and os.path.exists(voice)),
        "config_exists": bool(voice and os.path.exists(voice + ".json")),
        "piper_ready": piper_available(),
        "piper_sample_rate": _piper_sample_rate(voice) if voice else None,
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


# Blok karakter simbol/emoji yang DIBUANG sebelum TTS (biar suara bersih, tak
# membaca 'emoji' aneh, dan tak bikin espeak-ng/Piper tersandung).
_SYMBOL_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # emoji & pictographs (semua blok modern)
    "\U00002600-\U000027BF"   # misc symbols + dingbats (mis. thumbs, cek)
    "\U00002190-\U000021FF"   # panah
    "\U00002300-\U000023FF"   # technical (jam/alarm/tombol dsb)
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U00002122"              # (TM)
    "\U00002139"              # (info)
    "\U000024C2"              # (M dalam lingkaran)
    "\U0000FE00-\U0000FE0F"   # variation selectors (VS15/VS16)
    "\U0000200D"              # zero width joiner (perekat emoji)
    "\U000020E3"              # combining enclosing keycap (0..9 keycap)
    "\U0000FEFF"              # BOM / zero width no-break space
    "\U0000200B-\U0000200F"   # zero width space & tanda arah
    "]+",
    flags=re.UNICODE,
)


def _debug_surrogates(text):
    """Catat lokasi surrogate liar (diagnostik) sebelum dibersihkan. Fail-soft.

    Mencatat index, repr, dan code point tiap surrogate (dibatasi beberapa entri
    pertama agar log tak membanjir), meniru diagnosa 'INVALID SURROGATE ...'.
    """
    try:
        n = 0
        for i, ch in enumerate(text):
            code = ord(ch)
            if 0xD800 <= code <= 0xDFFF:
                _log("SURROGATE TAK VALID index=%d repr=%r code=U+%04X"
                     % (i, ch, code))
                n += 1
                if n >= 5:
                    _log("... surrogate tambahan disembunyikan dari log.")
                    break
    except Exception:
        pass


def _sanitize_for_tts(text):
    """Bersihkan teks agar AMAN & JELAS untuk TTS (khususnya Piper/espeak-ng).

    Urutan:
      1) Diagnostik + BUANG surrogate liar (mis. '\udc9d') penyebab crash
         'UnicodeEncodeError: surrogates not allowed' saat Python menyiapkan
         input subprocess Piper.
      2) BUANG emoji & simbol piktografik + variation selector + ZWJ + zero-width
         -> suara jadi teks bersih, tak membaca 'emoji'.
      3) Normalkan tanda baca tipografis (kutip lengkung, dash, elipsis, nbsp).
      4) Ubah underscore '_' jadi spasi (nama intent seperti
         'Administrasi_EFIN_Lupa' terbaca wajar), rapikan spasi ganda.
      5) Buang karakter kontrol non-cetak.
      6) Jaring pengaman: pastikan hasil valid UTF-8.
    Tanda baca kalimat biasa (.,!?;:-()\"') SENGAJA dipertahankan karena membantu
    intonasi TTS. Fail-soft: selalu kembalikan string yang bisa di-encode UTF-8.
    """
    if not text:
        return text
    # 1) surrogate: diagnosa lalu buang
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        _debug_surrogates(text)
    text = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    # 2) buang emoji & simbol piktografik + zero-width
    text = _SYMBOL_RE.sub("", text)
    # 3) normalisasi tipografi umum -> ASCII
    repl = {
        "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    # 4) underscore -> spasi (nama intent terbaca wajar)
    text = text.replace("_", " ")
    # 5) buang kontrol non-cetak (pertahankan tab/newline/CR + >= spasi)
    text = "".join(ch for ch in text
                   if ord(ch) in (9, 10, 13) or ord(ch) >= 32)
    # rapikan spasi ganda akibat penghapusan simbol
    text = re.sub(r"[ \t]{2,}", " ", text)
    # 6) jaring pengaman terakhir: pastikan valid UTF-8
    try:
        text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:
        pass
    return text.strip()


# ------------------------------------------------------------------ Piper
def _piper_sample_rate(voice):
    """Baca sample_rate dari <voice>.json (config Piper). Default 22050."""
    try:
        with open(voice + ".json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        sr = (cfg.get("audio", {}) or {}).get("sample_rate")
        if sr:
            return int(sr)
    except Exception:
        pass
    return 22050


def _wrap_pcm16_wav(pcm_bytes, sample_rate):
    """Bungkus raw PCM16 mono (bytes) jadi WAV lengkap (bytes)."""
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(int(sample_rate))
    w.writeframes(pcm_bytes)
    w.close()
    return buf.getvalue()


def _run_piper_file(binpath, voice, text, espeak):
    """Cadangan: Piper menulis ke file WAV (-f). Kembalikan (wav_bytes, error).

    Mode ini memakai penulis WAV internal Piper yang pada sebagian versi paket
    pip 'piper-tts' bisa gagal ('wave.Error: # channels not specified'); dipakai
    hanya bila mode --output-raw tak didukung binary.
    """
    out = tempfile.NamedTemporaryFile(prefix="vb_tts_", suffix=".wav",
                                      delete=False)
    out.close()
    try:
        cmd = [binpath, "-m", voice, "-f", out.name]
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


def _run_piper_once(binpath, voice, text):
    """Satu kali eksekusi Piper. Kembalikan (wav_bytes, error).

    Utama: --output-raw -> Piper menulis PCM16 mono mentah ke stdout, lalu kita
    bungkus jadi WAV di Python. Ini MENGHINDARI penulis WAV internal Piper yang
    bisa gagal dengan 'wave.Error: # channels not specified'. Bila binary tak
    mengenali flag raw, jatuh ke mode file (-f).
    """
    espeak = os.environ.get("VOICEBOT_PIPER_ESPEAK_DATA")
    sr = _piper_sample_rate(voice)
    raw_unsupported = False
    for raw_flag in ("--output-raw", "--output_raw"):
        try:
            cmd = [binpath, "-m", voice, raw_flag]
            if espeak:
                cmd += ["--espeak_data", espeak]
            p = subprocess.run(cmd, input=text.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=120)
            if p.returncode == 0 and p.stdout:
                return _wrap_pcm16_wav(p.stdout, sr), None
            err = p.stderr.decode("utf-8", "ignore").strip()
            low = err.lower()
            if ("unrecognized" in low or "unknown option" in low
                    or "no such option" in low or "invalid choice" in low
                    or "invalid option" in low):
                raw_unsupported = True
                continue  # coba varian flag berikutnya
            if p.returncode == 0 and not p.stdout:
                raw_unsupported = True
                break
            tail = err.splitlines()[-1] if err else "(tanpa pesan)"
            return None, "piper gagal (exit %s): %s" % (p.returncode, tail[:400])
        except subprocess.TimeoutExpired:
            return None, "piper timeout (>120 dtk)."
        except FileNotFoundError:
            return None, "Binary piper tidak bisa dijalankan: %s" % binpath
        except Exception as e:  # noqa: BLE001
            return None, str(e)
    if raw_unsupported:
        _log("Piper --output-raw tak didukung binary; memakai mode file (-f).")
    return _run_piper_file(binpath, voice, text, espeak)


def _synth_piper(text):
    """Sintesis via Piper dengan retry 1x. Kembalikan (wav_bytes, error).

    Kegagalan Piper sering bersifat TRANSIEN (subprocess/temp file/output kosong
    saat CPU sibuk). Karena itu dicoba ulang sekali (mesin SAMA) sebelum menyerah,
    dan setiap kegagalan dicatat ke log server agar turn 'gagal TTS' bisa
    didiagnosis.
    """
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

    last_err = None
    for attempt in range(2):  # 1 percobaan + 1 retry (mesin sama)
        data, err = _run_piper_once(binpath, voice, text)
        if data:
            if attempt > 0:
                _log("Piper sukses setelah retry.")
            return data, None
        last_err = err
        _log("Piper gagal (percobaan %d/2, teks %d char): %s"
             % (attempt + 1, len(text or ""), err))
    return None, last_err


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
    """Kembalikan (wav_bytes, error). wav_bytes None bila sintesis gagal.

    Mesin ditentukan setting `tts_engine`. Cadangan lintas-mesin (piper<->mms)
    HANYA dipakai bila setting `tts_cross_fallback` aktif (default mati) -- jadi
    secara default suara memakai mesin terpilih saja (tak berganti-ganti).
    Hasil sukses di-cache (lihat _cache_*) supaya frasa berulang tak disintesis
    ulang. Semua kegagalan dicatat ke log server.
    """
    text = (text or "").strip()
    if not text:
        return None, "teks kosong"

    # lapisan pelafalan + sanitasi sebelum TTS (tak mengubah teks ke klien)
    text = _pronounce(text)
    text = _sanitize_for_tts(text)
    if not text:
        return None, "teks kosong setelah sanitasi"

    # cache: frasa berulang (salam/konfirmasi/penjaga diam/filler) langsung pakai.
    cached = _cache_get(text)
    if cached is not None:
        return cached, None

    cross = _cross_fallback_on()

    if _engine() == "mms":
        data, err = _synth_mms(text)
        if data:
            _cache_put(text, data)
            return data, None
        if cross and piper_available():
            _log("MMS gagal (%s); cadangan lintas-mesin AKTIF -> memakai Piper." % err)
            data2, err2 = _synth_piper(text)
            if data2:
                _log("Piper (cadangan) sukses menghasilkan audio.")
                _cache_put(text, data2)
                return data2, None
            return None, "MMS gagal: %s | Piper gagal: %s" % (err, err2)
        _log("MMS gagal (cadangan lintas-mesin mati): %s" % err)
        return None, err

    # engine 'piper' (default)
    data, err = _synth_piper(text)
    if data:
        _cache_put(text, data)
        return data, None
    if cross and mms_available():
        _log("Piper gagal (%s); cadangan lintas-mesin AKTIF -> mencoba MMS." % err)
        data2, err2 = _synth_mms(text)
        if data2:
            _log("MMS (cadangan) sukses menghasilkan audio.")
            _cache_put(text, data2)
            return data2, None
        return None, "Piper gagal: %s | MMS gagal: %s" % (err, err2)
    _log("Piper gagal (cadangan lintas-mesin mati): %s" % err)
    return None, err


def warmup(text="Halo, selamat datang."):
    """Pra-muat mesin TTS aktif agar sintesis PERTAMA tidak 'dingin'.

    Untuk MMS ini memuat + men-cache model (VitsModel) sehingga giliran pertama
    tidak menanggung waktu load/unduh model (sumber utama kesan 'aplikasi macet').
    Untuk Piper ini memicu resolve binary + satu sintesis singkat. Aman dipanggil
    berulang (MMS memakai cache _MMS). Fail-soft.

    Kembalikan (ok, error): ok=True bila menghasilkan audio.
    """
    try:
        data, err = synth(text)
        return (bool(data), err)
    except Exception as e:  # noqa: BLE001
        return (False, str(e))
