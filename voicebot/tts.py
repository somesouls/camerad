# -*- coding: utf-8 -*-
"""voicebot/tts.py -- TTS LOKAL via Piper (offline).

Menyintesis WAV dari teks memakai Piper (binary lokal). Konfigurasi via env:
  VOICEBOT_PIPER_BIN   -- path/nama binary piper (default 'piper')
  VOICEBOT_PIPER_VOICE -- path model suara .onnx id-ID (WAJIB agar TTS aktif)

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


def available():
    if not _voice():
        return False
    b = _bin()
    return bool(shutil.which(b) or os.path.exists(b))


def synth(text):
    """Kembalikan (wav_bytes, error). wav_bytes None bila gagal/tak tersedia."""
    text = (text or "").strip()
    if not text:
        return None, "teks kosong"
    if not available():
        return None, ("Piper belum dikonfigurasi "
                      "(set VOICEBOT_PIPER_BIN & VOICEBOT_PIPER_VOICE).")
    out = tempfile.NamedTemporaryFile(prefix="vb_tts_", suffix=".wav", delete=False)
    out.close()
    try:
        cmd = [_bin(), "-m", _voice(), "-f", out.name]
        p = subprocess.run(cmd, input=text.encode("utf-8"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=60)
        if p.returncode != 0:
            return None, "piper gagal: %s" % (p.stderr.decode("utf-8", "ignore")[:200])
        with open(out.name, "rb") as f:
            return f.read(), None
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    finally:
        try:
            os.unlink(out.name)
        except Exception:
            pass
