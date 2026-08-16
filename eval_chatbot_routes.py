# -*- coding: utf-8 -*-
"""eval_chatbot_routes.py — Menu \"Evaluasi RAG · Chatbot\" (khusus profil chatbot).

Rute (area akses 'peraturan' = admin):
  GET  /rag-eval-chatbot                 -> halaman
  GET  /api/eval/chatbot/summary         -> daftar run + profil
  GET  /api/eval/chatbot/peak            -> puncak hit per jendela waktu (saran beban)
  POST /api/eval/chatbot/intent          -> metode 1 (coverage training-phrase top-intent)
  POST /api/eval/chatbot/fallback        -> metode 2 (deflection fallback + riwayat)
  POST /api/eval/chatbot/load            -> metode 3 (uji beban/concurrency)
  GET  /api/eval/chatbot/status?run=     -> progres run
  GET  /api/eval/chatbot/report?run=&only= -> metrik + hasil
  POST /api/eval/chatbot/stop            -> hentikan run {run}

  -- Metode 4: Peta Recall per-intent (tersimpan permanen) --
  POST /api/eval/chatbot/map/start       -> mulai pemetaan {profil,top_n,window,per_intent,only_unanswered,judge,limit}
  GET  /api/eval/chatbot/map/status?run= -> progres pemetaan
  GET  /api/eval/chatbot/map/list?status=&q= -> daftar intent + status
  GET  /api/eval/chatbot/map/intent?intent= -> detail per-frasa satu intent
  GET  /api/eval/chatbot/map/summary     -> ringkasan status
  POST /api/eval/chatbot/map/reset       -> batalkan status {intent} atau {all:true}
  POST /api/eval/chatbot/map/stop        -> hentikan pemetaan {run}

Daftarkan: import eval_chatbot_routes; eval_chatbot_routes.register(app)
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import eval_chatbot as ec
import eval_recall_map as erm
import rag_config_db as rcfg


async def _body(request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _int(v, default):
    try:
        return int(v)
    except Exception:
        return default


async def page(request: Request):
    return render_page(request, "eval_chatbot.html", "rag_eval_chatbot",
                       {"profil_list": rcfg.list_profiles()})


async def api_summary(request: Request):
    return JSONResponse({"ok": True, "runs": ec.list_runs(20), "profil": rcfg.list_profiles()})


async def api_peak(request: Request):
    ws = _int(request.query_params.get("window_seconds"), 5) or 5
    window = request.query_params.get("window") or "30d"
    try:
        return JSONResponse(await run_in_threadpool(ec.peak_hits, ws, window, None))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intent(request: Request):
    b = await _body(request)
    limit = b.get("limit")
    try:
        limit = int(limit) if limit not in (None, "", 0, "0") else None
    except Exception:
        limit = None
    try:
        r = await run_in_threadpool(
            ec.start_intent, (b.get("profil") or "chatbot").strip(),
            _int(b.get("top_n"), 100), (b.get("window") or "90d").strip(), None,
            _int(b.get("per_intent"), 12), bool(b.get("judge", True)), limit)
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_fallback(request: Request):
    b = await _body(request)
    try:
        r = await run_in_threadpool(
            ec.start_fallback, (b.get("profil") or "chatbot").strip(),
            (b.get("window") or "30d").strip(), _int(b.get("min_count"), 2),
            _int(b.get("limit"), 200), None, bool(b.get("judge", False)),
            bool(b.get("also_no_history", False)))
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_load(request: Request):
    b = await _body(request)
    try:
        r = await run_in_threadpool(
            ec.start_load, (b.get("profil") or "chatbot").strip(),
            (b.get("mode") or "tanpa_llm").strip(), _int(b.get("concurrency"), 20),
            _int(b.get("total"), 100), (b.get("question") or "").strip())
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_status(request: Request):
    run_id = (request.query_params.get("run") or "").strip()
    if not run_id:
        return JSONResponse({"ok": False, "error": "parameter run kosong"})
    try:
        return JSONResponse(await run_in_threadpool(ec.status, run_id))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_report(request: Request):
    run_id = (request.query_params.get("run") or "").strip() or None
    metode = (request.query_params.get("metode") or "").strip() or None
    only = (request.query_params.get("only") or "").strip() or None
    try:
        return JSONResponse(await run_in_threadpool(ec.report, run_id, metode, only))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_stop(request: Request):
    b = await _body(request)
    return JSONResponse(ec.stop((b.get("run") or "").strip()))


# ---------------------------------------------------------------- Metode 4: Peta Recall
async def api_map_start(request: Request):
    b = await _body(request)
    limit = b.get("limit")
    try:
        limit = int(limit) if limit not in (None, "", 0, "0") else None
    except Exception:
        limit = None
    try:
        r = await run_in_threadpool(
            erm.start_map, (b.get("profil") or "chatbot").strip(),
            _int(b.get("top_n"), 100), (b.get("window") or "90d").strip(), None,
            _int(b.get("per_intent"), 12), bool(b.get("only_unanswered", True)),
            bool(b.get("judge", True)), limit)
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_status(request: Request):
    run_id = (request.query_params.get("run") or "").strip()
    if not run_id:
        return JSONResponse({"ok": False, "error": "parameter run kosong"})
    try:
        return JSONResponse(await run_in_threadpool(erm.status, run_id))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_list(request: Request):
    status = (request.query_params.get("status") or "").strip() or None
    q = (request.query_params.get("q") or "").strip() or None
    try:
        return JSONResponse(await run_in_threadpool(erm.get_map, status, q, 1000))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_intent(request: Request):
    intent = (request.query_params.get("intent") or "").strip()
    if not intent:
        return JSONResponse({"ok": False, "error": "parameter intent kosong"})
    try:
        return JSONResponse(await run_in_threadpool(erm.get_intent, intent))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_summary(request: Request):
    try:
        return JSONResponse(await run_in_threadpool(erm.summary))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_reset(request: Request):
    b = await _body(request)
    try:
        r = await run_in_threadpool(
            erm.reset_status, (b.get("intent") or "").strip() or None,
            bool(b.get("all", False)))
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_map_stop(request: Request):
    b = await _body(request)
    return JSONResponse(erm.stop((b.get("run") or "").strip()))


def register(app):
    app.add_api_route("/rag-eval-chatbot", page, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/summary", api_summary, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/peak", api_peak, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/intent", api_intent, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/fallback", api_fallback, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/load", api_load, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/status", api_status, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/report", api_report, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/stop", api_stop, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/map/start", api_map_start, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/map/status", api_map_status, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/map/list", api_map_list, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/map/intent", api_map_intent, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/map/summary", api_map_summary, methods=["GET"])
    app.add_api_route("/api/eval/chatbot/map/reset", api_map_reset, methods=["POST"])
    app.add_api_route("/api/eval/chatbot/map/stop", api_map_stop, methods=["POST"])
