# -*- coding: utf-8 -*-
"""system_routes.py — Rute utilitas sistem: statistik pustaka & healthz (migrasi langkah 2).

Daftarkan dengan:
    import system_routes; system_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import knowledge.stats as pstats


def register(app):
    @app.get("/api/pustaka/stats")
    async def api_pustaka_stats(request: Request):
        """Statistik pemakaian pustaka pengetahuan (berapa sering tiap entri dipakai)."""
        def _run():
            conn = pstats.init_db(pstats.connect())
            try:
                return {"ok": True, "stats": pstats.stats(conn, top_n=8)}
            finally:
                conn.close()
        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "service": "dialogflow-avaya-pipeline-frontend"}
