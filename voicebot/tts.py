# -*- coding: utf-8 -*-
"""voicebot/tts.py -- TTS LOKAL via Piper (offline).

Menyintesis WAV dari teks memakai Piper (binary lokal). Konfigurasi via env:
  VOICEBOT_PIPER_BIN   -- path/nama binary piper (default 'piper').
                          Di WINDOWS sangat disarankan memakai piper.exe
                          STANDALONE (rilis resmi Piper), BUKAN paket pip
                          'piper-tts' yang sering gagal karena piper-phonemize.
  VOICEBOT_PIPER_VOICE -- path model suara .onnx id-ID (WAJIB agar TTS aktif).
                          File <voice>.onnx.json harus ada di folder yang sama.
  VOICEBOT_PIPER_ESPEAK_DATA -- (opsional) path folder espeak-ng-data.

Sebelum sintesis, teks dilewatkan lapisan pelafalan (voicebot.pron): kamus
singkatan + eja angka panjang. Bisa dimatikan via setting pron_enabled.

Fail-soft: bila Piper belum dikonfigurasi/terpasang, synth() -> (None, alasan)
sehingga engine tetap menjawab dalam bentuk teks (Lab tetap jalan).
"""
import os
import shutil
import subprocess
import tempfile


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


def available():
    if not _voice():
        return False
    return _resolve_bin() is not None


def diagnostics():
    """Info status TTS untuk halaman konfigurasi / health."""
    voice = _voice()
    return {
        "bin": _bin(),
        "bin_resolved": _resolve_bin(),
        "voice": voice,
        "voice_exists": bool(voice and os.path.exists(voice)),
        "config_exists": bool(voice and os.path.exists(voice + ".json")),
        "ready": available(),
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


def synth(text):
    """Kembalikan (wav_bytes, error). wav_bytes None bila gagal/tak tersedia."""
    text = (text or "").strip()
    if not text:
        return None, "teks kosong"

    # lapisan pelafalan sebelum TTS (tidak mengubah teks yang ditampilkan ke klien)
    text = _pronounce(text)

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
            # baris terakhir stderr biasanya pesan error yang sebenarnya
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
