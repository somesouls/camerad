# -*- coding: utf-8 -*-
"""voicebot/tts.py -- TTS lokal via Piper.

Menggunakan piper-tts yang terpasang di Python environment:
    python -m piper

Voice model:
    id_ID-news_tts-medium.onnx
    id_ID-news_tts-medium.onnx.json

Konfigurasi opsional via env:
    VOICEBOT_PIPER_VOICE
"""

import os
import subprocess
import tempfile
import sys
from pathlib import Path


# Folder project utama:
# C:\Users\USER\chatbot\pipeline_lokal
BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_VOICE = "id_ID-news_tts-medium"


def _voice():
    return os.environ.get("VOICEBOT_PIPER_VOICE") or DEFAULT_VOICE


def _model_path():
    voice = _voice()

    # Kalau environment berisi path .onnx, gunakan langsung.
    if voice.lower().endswith(".onnx"):
        return Path(voice)

    # Kalau hanya nama voice, cari di folder project.
    return BASE_DIR / f"{voice}.onnx"


def _config_path():
    model = _model_path()
    return Path(str(model) + ".json")


def available():
    """Cek apakah Piper + model tersedia."""
    try:
        # Pastikan module Piper bisa di-import.
        result = subprocess.run(
            [sys.executable, "-m", "piper", "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
        )

        if result.returncode != 0:
            return False

        model = _model_path()
        config = _config_path()

        return model.is_file() and config.is_file()

    except Exception:
        return False


def synth(text):
    """Kembalikan (wav_bytes, error).

    wav_bytes None bila TTS gagal/tidak tersedia.
    """
    text = (text or "").strip()

    if not text:
        return None, "teks kosong"

    model = _model_path()
    config = _config_path()

    if not available():
        return None, (
            "Piper/model belum tersedia. "
            f"Model={model}, Config={config}"
        )

    out = tempfile.NamedTemporaryFile(
        prefix="vb_tts_",
        suffix=".wav",
        delete=False,
    )
    out.close()

    try:
        cmd = [
            sys.executable,
            "-m",
            "piper",
            "-m",
            str(model),
            "-c",
            str(config),
            "-f",
            out.name,
        ]

        p = subprocess.run(
            cmd,
            input=text,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(BASE_DIR),
            timeout=60,
        )

        if p.returncode != 0:
            error = (p.stderr or p.stdout or "unknown error").strip()
            return None, f"piper gagal: {error[:500]}"

        if not os.path.isfile(out.name):
            return None, "Piper selesai tetapi file WAV tidak dibuat."

        if os.path.getsize(out.name) == 0:
            return None, "Piper membuat WAV kosong."

        with open(out.name, "rb") as f:
            return f.read(), None

    except subprocess.TimeoutExpired:
        return None, "Piper timeout setelah 60 detik."

    except Exception as e:
        return None, str(e)

    finally:
        try:
            os.unlink(out.name)
        except Exception:
            pass