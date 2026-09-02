# -*- coding: utf-8 -*-
"""voicebot/stt.py -- STT LOKAL (reuse avaya.phone_stt / faster-whisper).

Memakai kembali mesin STT yang sudah ada di proyek (faster-whisper, id-ID) lewat
avaya.phone_stt.transcribe_file. Menerima BYTES audio (dari upload/rekaman),
menulis ke berkas sementara, lalu mentranskrip. Fail-soft: bila STT belum siap
(mis. faster-whisper belum terpasang), kembalikan {ok: False, error}.
"""
import os
import tempfile


def available():
    try:
        import avaya.phone_stt  # noqa: F401
        return True
    except Exception:
        return False


def transcribe_bytes(data, filename="audio.wav", lang=None):
    lang = (lang or os.environ.get("AWE_STT_LANG") or "id").strip() or "id"
    if not data:
        return {"ok": False, "text": "", "error": "audio kosong"}
    try:
        import avaya.phone_stt as avstt
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "error": "STT lokal belum siap: %s" % e}
    ext = os.path.splitext(filename or "")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(prefix="vb_stt_", suffix=ext, delete=False)
    try:
        tmp.write(data)
        tmp.flush()
        tmp.close()
        tr = avstt.transcribe_file(tmp.name, lang=lang)
        if not tr or not tr.get("ok"):
            return {"ok": False, "text": "",
                    "error": (tr or {}).get("error") or "STT gagal"}
        return {"ok": True, "text": (tr.get("text") or "").strip(),
                "duration": tr.get("duration"), "device": tr.get("device")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "error": str(e)}
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
