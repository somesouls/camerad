# -*- coding: utf-8 -*-
"""voicebot/engine.py -- orkestrasi voice loop (Mode A, turn-based).

    audio/teks -> STT -> NLU hybrid -> (respons intent | LLM fallback)
               -> keputusan hand-off -> TTS -> log turn.

Sesi disimpan in-memory (cukup untuk tahap konsep, 1 proses). Semua komponen
berat di-impor LAZY + fail-soft. Reuse: voicebot.stt (faster-whisper),
common.llm_client (LLM lokal), handoff.routing_db (perutean layanan).
"""
import time
import uuid
import base64
import datetime as _dt

from voicebot import config_db as cfg
from voicebot import nlu as vb_nlu
from voicebot import stt as vb_stt
from voicebot import tts as vb_tts

_SESSIONS = {}


def _now_iso():
    return _dt.datetime.now().isoformat(timespec="seconds")


def create_session():
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = {"created_at": _now_iso(), "history": [], "fallback_streak": 0}
    return {"session_id": sid, "created_at": _SESSIONS[sid]["created_at"]}


def end_session(sid):
    _SESSIONS.pop(sid, None)
    return {"ok": True}


def _get_session(sid):
    if not sid or sid not in _SESSIONS:
        s = create_session()
        return s["session_id"], _SESSIONS[s["session_id"]]
    return sid, _SESSIONS[sid]


def _llm_fallback(text, sess, settings):
    """Jawaban terbuka via LLM lokal (common.llm_client). Fail-soft."""
    try:
        import common.llm_client as llm
        try:
            names = [i["name"] for i in cfg.list_intents()]
        except Exception:
            names = []
        sysmsg = settings.get("llm_system") or ""
        if names:
            sysmsg += ("\nIntent yang tersedia: " + ", ".join(names[:50]) +
                       ". Jika pertanyaan cocok salah satu, jawab sesuai konteks itu.")
        msgs = []
        for h in sess["history"][-6:]:
            if h.get("user"):
                msgs.append({"role": "user", "content": h.get("user", "")})
            if h.get("bot"):
                msgs.append({"role": "assistant", "content": h["bot"]})
        msgs.append({"role": "user", "content": text})
        ans = llm.chat(msgs, system=sysmsg, max_new_tokens=256, temperature=0.4)
        return (ans or "").strip() or (settings.get("fallback_reply") or "")
    except Exception as e:  # noqa: BLE001
        print("[voicebot.engine] LLM fallback gagal: %s" % e, flush=True)
        return settings.get("fallback_reply") or ""


def _check_handoff(text, sess, settings):
    tl = (text or "").lower()
    triggers = [t.strip().lower() for t in (settings.get("handoff_triggers") or "").split(",") if t.strip()]
    for t in triggers:
        if t and t in tl:
            return True, "permintaan eksplisit ke agen"
    try:
        maxfb = int(settings.get("handoff_max_fallback") or 2)
    except Exception:
        maxfb = 2
    if maxfb and sess.get("fallback_streak", 0) >= maxfb:
        return True, "gagal dipahami %d kali beruntun" % sess.get("fallback_streak", 0)
    try:
        import handoff.routing_db as hrdb
        row = hrdb.match_routing(text)
        if row:
            return True, "cocok intent layanan: %s" % (row.get("top_intent") or "")
    except Exception:
        pass
    return False, ""


def talk(session_id=None, text=None, audio_bytes=None, audio_filename="audio.wav",
         want_audio=True):
    """Satu giliran percakapan. Kembalikan dict respons (JSON-able)."""
    t0 = time.time()
    settings = cfg.get_settings()
    sid, sess = _get_session(session_id)

    transkrip = (text or "").strip()
    stt_err = None
    if not transkrip and audio_bytes:
        if str(settings.get("stt_enabled", "1")) != "0":
            tr = vb_stt.transcribe_bytes(audio_bytes, audio_filename,
                                         lang=settings.get("stt_lang") or "id")
            transkrip = tr.get("text") or ""
            if not tr.get("ok"):
                stt_err = tr.get("error")
        else:
            stt_err = "STT dimatikan di konfigurasi"

    try:
        threshold = float(settings.get("threshold") or 0.6)
    except Exception:
        threshold = 0.6

    cls = {"intent": None, "score": 0.0, "response": "", "engine": "none"}
    if transkrip:
        cls = vb_nlu.classify(transkrip)
    intent = cls.get("intent")
    confidence = float(cls.get("score") or 0.0)
    engine = cls.get("engine")

    if intent and confidence >= threshold:
        jawaban = (cls.get("response") or cfg.intent_response(intent)
                   or settings.get("fallback_reply") or "")
        sumber = "nlu"
        sess["fallback_streak"] = 0
    else:
        sumber = "llm"
        intent = None
        if transkrip:
            jawaban = _llm_fallback(transkrip, sess, settings)
        else:
            jawaban = settings.get("fallback_reply") or "Maaf, suara tidak terdengar."
        sess["fallback_streak"] = sess.get("fallback_streak", 0) + 1

    do_handoff, reason = _check_handoff(transkrip, sess, settings)
    action = "handoff" if do_handoff else "reply"
    handoff = None
    if do_handoff:
        parts = [("U: " + (h.get("user") or "")) for h in sess["history"][-3:]]
        parts.append("U: " + transkrip)
        handoff = {"reason": reason, "ringkasan": (" | ".join(parts))[:1000]}

    audio_b64 = None
    tts_err = None
    if want_audio and jawaban and str(settings.get("tts_enabled", "1")) != "0":
        wav, tts_err = vb_tts.synth(jawaban)
        if wav:
            audio_b64 = base64.b64encode(wav).decode("ascii")

    sess["history"].append({"user": transkrip, "bot": jawaban})
    id_trace = sid + "-" + str(len(sess["history"]))
    try:
        cfg.log_turn({
            "session_id": sid, "id_trace": id_trace, "user_text": transkrip,
            "intent": intent, "confidence": confidence, "sumber": sumber,
            "bot_text": jawaban, "handoff": do_handoff,
        })
    except Exception:
        pass

    return {
        "session_id": sid,
        "transkrip": transkrip,
        "intent": intent,
        "confidence": round(confidence, 3),
        "sumber": sumber,
        "engine": engine,
        "jawaban_teks": jawaban,
        "jawaban_audio_b64": audio_b64,
        "action": action,
        "handoff": handoff,
        "stt_error": stt_err,
        "tts_error": tts_err,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }
