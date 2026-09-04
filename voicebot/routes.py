# -*- coding: utf-8 -*-
"""voicebot/routes.py -- halaman & API Voicebot (config, lab, engine).

Halaman:
  GET /voicebot        -> konfigurasi mesin + kelola intent + kamus + log terbaru
  GET /voicebot/lab    -> lab uji suara/teks end-to-end

API engine (Mode A):
  POST /api/voicebot/session  -> buat sesi (+ salam pembuka bila dialog aktif)
  POST /api/voicebot/talk     -> multipart (audio) atau JSON (text) + session_id
  POST /api/voicebot/end      -> tutup sesi
  GET  /api/voicebot/health   -> status STT/TTS (+ diagnostik mesin TTS)
  GET  /api/voicebot/filler   -> klip filler (teks + audio) utk tutup latency
  GET  /api/voicebot/greeting -> teks + audio salam pembuka (dipakai klien Mode A)
  POST /api/voicebot/warmup   -> pra-muat mesin TTS agar giliran pertama tak dingin

API engine (Mode B):
  WS   /api/voicebot/stream   -> percakapan suara real-time + barge-in
                                 (lihat voicebot/stream.py utk protokol)

API kelola:
  GET  /api/voicebot/config              -> ambil konfigurasi
  POST /api/voicebot/config/save         -> simpan konfigurasi
  GET  /api/voicebot/config/export       -> unduh cadangan konfigurasi (JSON)
  POST /api/voicebot/config/import       -> terapkan cadangan konfigurasi (JSON)
  POST /api/voicebot/intents/list        -> daftar intent (+cari)
  POST /api/voicebot/intents/save        -> tambah/ubah intent
  POST /api/voicebot/intents/delete      -> hapus intent
  POST /api/voicebot/intents/df-preview  -> pratinjau intent tersibuk Dialogflow
  POST /api/voicebot/intents/df-import   -> impor intent tersibuk -> vb_intents
  POST /api/voicebot/lexicon/list        -> daftar kamus pelafalan (+cari)
  POST /api/voicebot/lexicon/save        -> tambah/ubah istilah pelafalan
  POST /api/voicebot/lexicon/delete      -> hapus istilah pelafalan
  POST /api/voicebot/pron/preview        -> pratinjau hasil normalisasi teks
  POST /api/voicebot/logs                -> log turn terbaru

Akses admin diatur di app_core._route_area (area 'peraturan'). Endpoint
/api/voicebot/* juga bisa dipanggil klien luar (APK) via header X-API-Key
(lihat bypass di app_core._auth_middleware). Daftarkan:
    import voicebot.routes as voicebot_routes; voicebot_routes.register(app)
"""
import base64
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app_core import render_page

from voicebot import config_db as cfg
from voicebot import engine as vb_engine
from voicebot import stt as vb_stt
from voicebot import tts as vb_tts
from voicebot import pron as vb_pron
from voicebot import dialog as vb_dialog
from voicebot import df_import as vb_dfimport
from voicebot import stream as vb_stream


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
    """Status STT/TTS + diagnostik mesin TTS (agar 'tidak ada suara' cepat terdeteksi)."""
    try:
        diag = vb_tts.diagnostics()
    except Exception:
        diag = {}
    return JSONResponse({"ok": True, "service": "voicebot",
                        "stt_ready": vb_stt.available(),
                        "tts_ready": vb_tts.available(),
                        "tts_engine": diag.get("engine"),
                        "tts": diag})


async def api_warmup(request: Request):
    """Pra-muat mesin TTS aktif supaya sintesis PERTAMA tidak 'dingin'
    (MMS memuat model; Piper resolve binary). Aman dipanggil berulang.

    Bila 'pregen_enabled' aktif (Poin 3.2), warmup juga menghangatkan cache
    shorten + TTS untuk frasa yang sering dipakai (salam/penutup/filler/
    konfirmasi/jawaban intent). pregen_answers() self-guarded & fail-soft -> no-op
    bila mati, jadi endpoint ini tetap aman dipanggil berulang."""
    try:
        ok, err = await run_in_threadpool(vb_tts.warmup)
        pregen = None
        try:
            settings = await run_in_threadpool(cfg.get_settings)
            pregen = await run_in_threadpool(vb_engine.pregen_answers, settings)
        except Exception:
            pregen = None
        diag = {}
        try:
            diag = vb_tts.diagnostics()
        except Exception:
            diag = {}
        return JSONResponse({"ok": True, "tts_warm": bool(ok),
                            "tts_error": err, "tts_engine": diag.get("engine"),
                            "pregen": pregen,
                            "tts": diag})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_filler(request: Request):
    """Klip filler (teks + audio base64) utk diputar klien saat jawaban dihitung."""
    want_audio = True
    idx = None
    qp = request.query_params
    if qp.get("want_audio") is not None:
        want_audio = str(qp.get("want_audio")) not in ("0", "false", "False")
    if qp.get("index") is not None:
        idx = qp.get("index")
    try:
        res = await run_in_threadpool(vb_engine.get_filler, want_audio, idx)
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_greeting(request: Request):
    """Teks + audio salam pembuka utk diputar klien (Mode A) saat sesi baru.

    Mengembalikan {enabled, text, audio_b64, tts_error}. Bila dialog manager mati,
    enabled=False. Audio memakai mesin TTS voicebot (bukan TTS bawaan perangkat).
    """
    want_audio = True
    qp = request.query_params
    if qp.get("want_audio") is not None:
        want_audio = str(qp.get("want_audio")) not in ("0", "false", "False")

    def _run():
        settings = cfg.get_settings()
        if not vb_dialog.enabled(settings):
            return {"enabled": False, "text": "", "audio_b64": None,
                    "tts_error": None}
        text = vb_dialog.greeting(settings)
        audio_b64 = None
        tts_err = None
        if want_audio and text and str(settings.get("tts_enabled", "1")) != "0":
            wav, tts_err = vb_tts.synth(text)
            if wav:
                audio_b64 = base64.b64encode(wav).decode("ascii")
        return {"enabled": True, "text": text, "audio_b64": audio_b64,
                "tts_error": tts_err}

    try:
        res = await run_in_threadpool(_run)
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


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
        # konfigurasi berubah -> kosongkan cache TTS supaya suara/parameter baru
        # langsung dipakai (fail-soft, tak menggagalkan simpan).
        try:
            vb_tts.clear_cache()
        except Exception:
            pass
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_config_export(request: Request):
    """Unduh cadangan konfigurasi (settings + intents + kamus) sebagai berkas JSON.

    Dikirim sebagai attachment 'voicebot-config.json' supaya langsung terunduh di
    browser. Simpan berkas ini sebagai cadangan; pulihkan lewat /config/import.
    """
    try:
        data = await run_in_threadpool(cfg.export_config)
        body = json.dumps(data, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=voicebot-config.json"
            },
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_config_import(request: Request):
    """Terapkan cadangan konfigurasi dari body JSON {data, mode}.

    - data: objek hasil /config/export (berisi settings/intents/lexicon).
    - mode: 'merge' (default, timpa+tambah) atau 'replace' (kosongkan intent &
      kamus dulu, lalu isi ulang dari data). Setelah impor, cache NLU direset &
      cache TTS dikosongkan supaya perubahan langsung berlaku.
    """
    b = await _json_body(request)
    data = b.get("data")
    mode = b.get("mode") or "merge"
    if not isinstance(data, dict):
        return JSONResponse(
            {"ok": False,
             "error": "Field 'data' (objek konfigurasi hasil ekspor) wajib diisi."}
        )
    try:
        res = await run_in_threadpool(cfg.import_config, data, mode)
        try:
            vb_nlu_reset()
        except Exception:
            pass
        try:
            vb_tts.clear_cache()
        except Exception:
            pass
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


# ------------------------------------------------------- impor dari Dialogflow
async def api_intents_df_preview(request: Request):
    b = await _json_body(request)
    try:
        rows = await run_in_threadpool(
            vb_dfimport.preview_top_intents,
            int(b.get("limit") or 50),
            (b.get("start") or None), (b.get("end") or None),
            (b.get("lang") or None),
            bool(b.get("include_system")), bool(b.get("include_umum")),
        )
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intents_df_import(request: Request):
    b = await _json_body(request)

    def _run():
        return vb_dfimport.import_top_intents(
            limit=int(b.get("limit") or 50),
            max_phrases=int(b.get("max_phrases") or 40),
            min_count=int(b.get("min_count") or 1),
            start=(b.get("start") or None),
            end=(b.get("end") or None),
            lang=(b.get("lang") or None),
            include_system=bool(b.get("include_system")),
            include_umum=bool(b.get("include_umum")),
            skip_existing=bool(b.get("skip_existing")),
            activate=(False if b.get("activate") in (0, "0", False, "false") else True),
        )

    try:
        res = await run_in_threadpool(_run)
        try:
            vb_nlu_reset()
        except Exception:
            pass
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


# ---------------------------------------------------------------- lexicon API
async def api_lexicon_list(request: Request):
    b = await _json_body(request)
    try:
        rows = await run_in_threadpool(cfg.list_lexicon, (b.get("q") or "").strip())
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_lexicon_save(request: Request):
    b = await _json_body(request)
    if not str(b.get("pattern") or "").strip():
        return JSONResponse({"ok": False, "error": "Field 'pattern' wajib diisi."})
    try:
        res = await run_in_threadpool(cfg.upsert_lexicon, b)
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_lexicon_delete(request: Request):
    b = await _json_body(request)
    if not b.get("id"):
        return JSONResponse({"ok": False, "error": "Field 'id' wajib diisi."})
    try:
        res = await run_in_threadpool(cfg.delete_lexicon, b.get("id"))
        return JSONResponse({"ok": True, **res})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


async def api_pron_preview(request: Request):
    b = await _json_body(request)
    try:
        txt = await run_in_threadpool(vb_pron.normalize, (b.get("text") or ""))
        return JSONResponse({"ok": True, "normalized": txt})
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
    app.add_api_route("/api/voicebot/warmup", api_warmup, methods=["POST"])
    app.add_api_route("/api/voicebot/filler", api_filler, methods=["GET"])
    app.add_api_route("/api/voicebot/greeting", api_greeting, methods=["GET"])
    app.add_api_route("/api/voicebot/config", api_config_get, methods=["GET"])
    app.add_api_route("/api/voicebot/config/save", api_config_save, methods=["POST"])
    app.add_api_route("/api/voicebot/config/export", api_config_export, methods=["GET"])
    app.add_api_route("/api/voicebot/config/import", api_config_import, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/list", api_intents_list, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/save", api_intents_save, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/delete", api_intents_delete, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/df-preview", api_intents_df_preview, methods=["POST"])
    app.add_api_route("/api/voicebot/intents/df-import", api_intents_df_import, methods=["POST"])
    app.add_api_route("/api/voicebot/lexicon/list", api_lexicon_list, methods=["POST"])
    app.add_api_route("/api/voicebot/lexicon/save", api_lexicon_save, methods=["POST"])
    app.add_api_route("/api/voicebot/lexicon/delete", api_lexicon_delete, methods=["POST"])
    app.add_api_route("/api/voicebot/pron/preview", api_pron_preview, methods=["POST"])
    app.add_api_route("/api/voicebot/logs", api_logs, methods=["POST"])
    app.add_api_websocket_route("/api/voicebot/stream", vb_stream.handle)
