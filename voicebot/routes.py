# -*- coding: utf-8 -*-
"""voicebot/routes.py -- halaman & API Voicebot (config, lab, engine).

Halaman:
  GET /voicebot        -> konfigurasi mesin + kelola intent + log terbaru
  GET /voicebot/lab    -> lab uji suara/teks end-to-end

API engine (Mode A):
  POST /api/voicebot/session  -> buat sesi
  POST /api/voicebot/talk     -> multipart (audio) atau JSON (text) + session_id
  POST /api/voicebot/end      -> tutup sesi
  GET  /api/voicebot/health   -> status STT/TTS

API kelola:
  GET  /api/voicebot/config           -> ambil konfigurasi
  POST /api/voicebot/config/save      -> simpan konfigurasi
  POST /api/voicebot/intents/list     -> daftar intent (+cari)
  POST /api/voicebot/intents/save     -> tambah/ubah intent
  POST /api/voicebot/intents/delete   -> hapus intent
  POST /api/voicebot/logs             -> log turn terbaru

Akses admin diatur di app_core._route_area (area 'peraturan'). Endpoint
/api/voicebot/* juga bisa dipanggil klien luar (APK) via header X-API-Key
(lihat bypass di app_core._auth_middleware). Daftarkan:
    import voicebot.routes as voicebot_routes; voicebot_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

from voicebot import config_db as cfg
from voicebot import engine as vb_engine
from voicebot import stt as vb_stt
from voicebot import tts as vb_tts


async def _json_body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


# ---------------------------------------------------------------- halaman
async def page_voicebot(request: Request):
    extra = {"n_intent": 0}
    try:
        extra["n_intent"] = len(cfg.list_intents())
    except Exception:
        pass
    return render_page(request, "voicebot.html", "voicebot", extra)


async def page_voicebot_lab(request: Request):
    return render_page(request, "voicebot_lab.html", "voicebot_lab")


# ---------------------------------------------------------------- engine API
async def api_session(request: Request):
    return JSONResponse(vb_engine.create_session())


async def api_end(request: Request):
    b = await _json_body(request)
    return JSONResponse(vb_engine.end_session(b.get("session_id")))


async def api_talk(request: Request):
    ct = (request.headers.get("content-type") or "").lower()
    text = sid = None
    audio_bytes = None
    audio_name = "audio.wav"
    want_audio = True
    if "application/json" in ct:
        b = await _json_body(request)
        text = b.get("text")
        sid = b.get("session_id")
        if b.get("want_audio") is not None:
            want_audio = bool(b.get("want_audio"))
    else:
        try:
            form = await request.form()
        except Exception:
            form = {}
        text = form.get("text")
        sid = form.get("session_id")
        wa = form.get("want_audio")
        if wa is not None:
            want_audio = str(wa) not in ("0", "false", "False")
        up = form.get("audio")
        if up is not None and hasattr(up, "read"):
            audio_bytes = await up.read()
            audio_name = getattr(up, "filename", None) or "audio.wav"
    try:
        res = await run_in_threadpool(
            vb_engine.talk, sid, text, audio_bytes, audio_name, want_audio
        )
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_health(request: Request):
    return JSONResponse({"ok": True, "service": "voicebot",
                        "stt_ready": vb_stt.available(),
                        "tts_ready": vb_tts.available()})


# ---------------------------------------------------------------- config API
async def api_config_get(request: Request):
    try:
        s = await run_in_threadpool(cfg.get_settings)
        return JSONResponse({"ok": True, "settings": s,
                            "stt_ready": vb_stt.available(),
                            "tts_ready": vb_tts.available()})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_config_save(request: Request):
    b = await _json_body(request)
    try:
        res = await run_in_threadpool(cfg.set_settings, b)
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


# ---------------------------------------------------------------- intents API
async def api_intents_list(request: Request):
    b = await _json_body(request)
    try:
        rows = await run_in_threadpool(cfg.list_intents, (b.get("q") or "").strip())
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intents_save(request: Request):
    b = await _json_body(request)
    if not str(b.get("name") or "").strip():
        return JSONResponse({"ok": False, "error": "Field 'name' wajib diisi."})
    try:
        res = await run_in_threadpool(cfg.upsert_intent, b)
        try:
            vb_nlu_reset()
        except Exception:
            pass
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intents_delete(request: Request):
    b = await _json_body(request)
    if not b.get("id"):
        return JSONResponse({"ok": False, "error": "Field 'id' wajib diisi."})
    try:
        res = await run_in_threadpool(cfg.delete_intent, b.get("id"))
        try:
            vb_nlu_reset()
        except Exception:
            pass
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


def vb_nlu_reset():
    from voicebot import nlu as _n
    _n.reset_cache()


async def api_logs(request: Request):
    b = await _json_body(request)
    try:
        rows = await run_in_threadpool(cfg.list_turns, int(b.get("limit") or 50))
        return JSONResponse({"ok": True, "rows": rows})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/voicebot", page_voicebot, methods=["GET"])
    app.add_api_route("/voicebot/lab", page_voicebot_lab, methods=["GET"])
    app.add_api_route("/api/voicebot/session", api_session, methods=["POST"])
    app.add_api_route("/api/voicebot/talk", api_talk, methods=["POST"])
    app.add_api_route("/api/voicebot/end", api_end, methods=["POST"])
    app.add_api_route("/api/voicebot/health", api_health, methods=["GET"])
    app.add_api_route("/api/voicebot/config", api_config_get, methods=["GET"])
    app.add_api_route("/api/voicebot/config/save", api_config_save, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/list", api_intents_list, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/save", api_intents_save, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/delete", api_intents_delete, methods=["POST"])
    app.add_api_route("/api/voicebot/logs", api_logs, methods=["POST"])
