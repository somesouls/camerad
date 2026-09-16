# -*- coding: utf-8 -*-
"""analytics_routes.py — Dashboard, ringkasan analitik, dan Analisis Deflection
(Epik D). Migrasi langkah 4 dari web_app.py.

Daftarkan dengan:
    import analytics_routes; analytics_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import db.analytics_db as adb
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


# =====================================================================
# Menu "Detail Percakapan" (Dialogflow): daftar sesi percakapan + transkrip
# ---------------------------------------------------------------------
# Halaman df_percakapan.html menampilkan tiap sesi (session_id) sebagai
# accordion; isi transkrip dimuat via /api/deflection/transcript. Endpoint
# di bawah menyediakan DAFTAR sesi (read-only) dari tabel `interactions`,
# lengkap dengan klasifikasi perjalanan (mandiri / fallback / ke agent),
# frasa pertama, jumlah interaksi, dan jumlah fallback. Bersifat ADITIF &
# non-breaking terhadap route lain.
# =====================================================================
async def api_percakapan_list(request: Request):
    q = request.query_params
    preset = q.get("range", "7d")
    start = q.get("start") or None
    end = q.get("end") or None
    lang = q.get("lang") or None
    term = (q.get("q") or "").strip()
    cat = (q.get("filter") or "all").strip().lower()
    try:
        limit = int(q.get("limit", 500))
    except Exception:
        limit = 500
    if limit <= 0 or limit > 2000:
        limit = 500
    s, e = adb.resolve_range(preset, start, end)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            where, params = adb._range_where(s, e)
            where = adb._lang_where(where, params, lang)
            if term:
                where += (" AND " if where else " WHERE ") + \
                    "session_id IN (SELECT session_id FROM interactions WHERE user_phrase LIKE ?)"
                params.append("%" + term + "%")
            sql = (
                "SELECT session_id, COUNT(*) AS n, "
                "MIN(ts) AS ts_first, MAX(ts) AS ts_last, "
                "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS fb1, "
                "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS fb2, "
                "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS agent, "
                "MAX(CASE WHEN intent_name=? THEN 1 ELSE 0 END) AS connector, "
                "SUM(CASE WHEN is_fallback=1 THEN 1 ELSE 0 END) AS fb_count, "
                "SUM(CASE WHEN is_fallback=0 AND substr(intent_name,1,7)<>'System_' "
                "AND substr(intent_name,1,5)<>'Umum_' THEN 1 ELSE 0 END) AS clean_hits, "
                "(SELECT user_phrase FROM interactions i2 WHERE i2.session_id=interactions.session_id "
                "AND TRIM(COALESCE(i2.user_phrase,''))<>'' ORDER BY i2.ts ASC, i2.insert_id ASC LIMIT 1) AS first_phrase "
                "FROM interactions" + where + " GROUP BY session_id"
            )
            head = [adb.FALLBACK_1, adb.FALLBACK_2, adb.AGENT_1500200, adb.AGENT_CONNECTOR]
            rows = conn.execute(sql, head + params).fetchall()
            items = []
            for r in rows:
                if r["agent"]:
                    category = "agent_1500200"
                elif r["connector"]:
                    category = "agent_connector"
                elif r["fb2"]:
                    category = "fallback2_no_agent"
                elif r["fb1"]:
                    category = "fallback_abandon"
                else:
                    category = "self_served"
                items.append({
                    "session_id": r["session_id"],
                    "n": r["n"],
                    "ts_first": r["ts_first"], "ts_last": r["ts_last"],
                    "fb_count": r["fb_count"] or 0,
                    "clean_hits": r["clean_hits"] or 0,
                    "agent": bool(r["agent"] or r["connector"]),
                    "category": category,
                    "category_label": adb.JOURNEY_LABELS.get(category, category),
                    "first_phrase": r["first_phrase"] or "",
                })
            if cat == "fallback":
                items = [it for it in items if it["fb_count"] > 0]
            elif cat == "agent":
                items = [it for it in items if it["agent"]]
            elif cat == "self":
                items = [it for it in items if it["category"] == "self_served"]
            items.sort(key=lambda it: (it["ts_first"] or ""), reverse=True)
            total = len(items)
            items = items[:limit]
            return {"ok": True, "range": {"start": s, "end": e},
                    "total": total, "shown": len(items), "items": items}
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
    app.add_api_route("/api/percakapan/list", api_percakapan_list, methods=["GET"])
