# -*- coding: utf-8 -*-
"""analytics_routes.py — Dashboard, ringkasan analitik, dan Analisis Deflection
(Epik D). Migrasi langkah 4 dari web_app.py.

Daftarkan dengan:
    import analytics_routes; analytics_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import analytics_db as adb
from app_core import render_page


def analytics_summary(preset, start, end, lang=None, inc_system=False, inc_umum=False):
    """Kumpulan metrik untuk dashboard, dijalankan di threadpool."""
    s, e = adb.resolve_range(preset, start, end)
    conn = adb.init_db(adb.connect())
    try:
        cov = adb.range_status(conn, s, e, (lang or "id")) if (s and e) else None
        return {
            "ok": True,
            "range": {"preset": preset or "", "start": s, "end": e},
            "lang": lang or "",
            "inc_system": bool(inc_system),
            "inc_umum": bool(inc_umum),
            "coverage": cov,
            "overview": adb.overview(conn, s, e, lang=lang, include_system=inc_system, include_umum=inc_umum),
            "top_intents": adb.top_intents(conn, s, e, 100, include_system=inc_system, include_umum=inc_umum, lang=lang),
            "volume": adb.volume_by_day(conn, s, e, lang=lang),
            "new_questions": adb.new_questions(conn, s, e, 200, lang=lang),
            "hot_topics": adb.hot_topics(conn, s, e, 20, lang=lang),
            "bounds": adb.data_bounds(conn),
            "last_ingest": adb.get_meta(conn, "last_ingest_at"),
            "last_range": adb.get_meta(conn, "last_ingest_range"),
        }
    finally:
        conn.close()


async def dashboard(request: Request):
    return render_page(request, "dashboard.html", "dashboard")


async def api_analytics_summary(request: Request):
    q = request.query_params
    preset = q.get("range", "7d")
    start = q.get("start") or None
    end = q.get("end") or None
    lang = q.get("lang") or None
    inc_system = (q.get("inc_system") in ("1", "true", "on"))
    inc_umum = (q.get("inc_umum") in ("1", "true", "on"))
    try:
        return JSONResponse(await run_in_threadpool(analytics_summary, preset, start, end, lang, inc_system, inc_umum))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_search_intents(request: Request):
    term = request.query_params.get("q", "").strip()
    if not term:
        return JSONResponse({"ok": False, "error": "Parameter q wajib."})

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "term": term, "results": adb.search_intents(conn, term, 25)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def deflection_page(request: Request):
    return render_page(request, "deflection.html", "deflection")


def _defl_range(q):
    preset = q.get("range", "30d")
    start = q.get("start") or None
    end = q.get("end") or None
    lang = q.get("lang") or None
    s, e = adb.resolve_range(preset, start, end)
    return s, e, lang


async def api_deflection_summary(request: Request):
    q = request.query_params
    s, e, lang = _defl_range(q)
    try:
        ws = int(q.get("work_start", 8))
        we = int(q.get("work_end", 16))
    except Exception:
        ws, we = 8, 16
    wd_raw = q.get("work_days", "1,2,3,4,5")
    try:
        wd = tuple(int(x) for x in wd_raw.split(",") if x.strip() != "")
    except Exception:
        wd = (1, 2, 3, 4, 5)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            data = adb.deflection_overview(conn, s, e, lang, wd, ws, we)
            data["ok"] = True
            data["range"] = {"start": s, "end": e}
            return data
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


async def api_deflection_candidates(request: Request):
    q = request.query_params
    s, e, lang = _defl_range(q)
    try:
        limit = int(q.get("limit", 200))
    except Exception:
        limit = 200

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "items": adb.candidate_list(conn, s, e, lang, limit=limit)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


async def api_deflection_candidate(request: Request):
    q = request.query_params
    phrase = q.get("phrase", "")
    if not phrase.strip():
        return JSONResponse({"ok": False, "error": "Parameter phrase wajib."})
    s, e, lang = _defl_range(q)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "detail": adb.candidate_detail(conn, phrase, s, e, lang)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


async def api_deflection_transcript(request: Request):
    sid = request.query_params.get("session_id", "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "Parameter session_id wajib."})

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "session_id": sid, "turns": adb.session_transcript(conn, sid)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


async def api_deflection_status_save(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    phrase = (body.get("phrase") or "").strip()
    if not phrase:
        return JSONResponse({"ok": False, "error": "phrase kosong."})
    status = (body.get("status") or "").strip().lower()
    note = body.get("note") or ""
    _u = getattr(request.state, "user", None) or {}
    who = (_u.get("nama") or _u.get("username") or "").strip()

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return adb.set_candidate_status(conn, phrase, status, note, who)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})

def register(app):
    app.add_api_route("/dashboard", dashboard, methods=["GET"])
    app.add_api_route("/api/analytics/summary", api_analytics_summary, methods=["GET"])
    app.add_api_route("/api/analytics/search-intents", api_search_intents, methods=["GET"])
    app.add_api_route("/deflection", deflection_page, methods=["GET"])
    app.add_api_route("/api/deflection/summary", api_deflection_summary, methods=["GET"])
    app.add_api_route("/api/deflection/candidates", api_deflection_candidates, methods=["GET"])
    app.add_api_route("/api/deflection/candidate", api_deflection_candidate, methods=["GET"])
    app.add_api_route("/api/deflection/transcript", api_deflection_transcript, methods=["GET"])
    app.add_api_route("/api/deflection/status/save", api_deflection_status_save, methods=["POST"])
