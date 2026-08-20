# -*- coding: utf-8 -*-
"""lifecycle_routes.py — Rute siklus-hidup intent (Epik E). Migrasi langkah 3.

Daftarkan dengan:
    import lifecycle_routes; lifecycle_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import intentmap_db as imdb
from app_core import render_page


async def lifecycle_page(request: Request):
    return render_page(request, "lifecycle.html", "lifecycle")


def _lc_months(v, d=6):
    try:
        n = int(float(v))
        return n if n > 0 else d
    except Exception:
        return d


def _lc_audit_user(request):
    u = getattr(request.state, "user", None)
    if u is None:
        return ""
    return (getattr(u, "nama", None) or getattr(u, "username", None) or "")


async def api_lifecycle_summary(request: Request):
    """Ringkasan siklus-hidup intent (Epik E). Menyegarkan last_called_at dari interactions."""
    q = request.query_params
    months = _lc_months(q.get("months"), 6)
    do_refresh = str(q.get("refresh") or "1").lower() not in ("0", "false", "no", "")

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            refreshed = None
            if do_refresh:
                try:
                    refreshed = imdb.refresh_lifecycle(conn)
                except Exception as _e:
                    refreshed = {"error": str(_e)}
            return {"ok": True, "overview": imdb.lifecycle_overview(conn, retensi_bulan=months),
                    "refreshed": refreshed}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_lifecycle_list(request: Request):
    """Daftar intent + status siklus-hidup (dipanggil / tidak / kandidat retensi / soft-deleted)."""
    q = request.query_params
    months = _lc_months(q.get("months"), 6)

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            items = imdb.lifecycle_list(
                conn,
                filt=(q.get("filter") or "all"),
                q=(q.get("q") or None),
                lang=(q.get("lang") or None),
                limit=imdb._to_int(q.get("limit"), 1000),
                retensi_bulan=months,
            )
            return {"ok": True, "items": items,
                    "overview": imdb.lifecycle_overview(conn, retensi_bulan=months)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_lifecycle_softdelete_save(request: Request):
    """Tandai/pulihkan soft-delete intent (butuh peran dengan hak edit). Body: {id|intent, soft_deleted}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})
    ident = ((body.get("id") or body.get("intent") or "")).strip()
    deleted = bool(body.get("soft_deleted", True))
    uname = _lc_audit_user(request)

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return imdb.set_soft_delete(conn, ident, deleted=deleted, user=uname)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

def register(app):
    app.add_api_route("/lifecycle", lifecycle_page, methods=["GET"])
    app.add_api_route("/api/lifecycle/summary", api_lifecycle_summary, methods=["GET"])
    app.add_api_route("/api/lifecycle/list", api_lifecycle_list, methods=["GET"])
    app.add_api_route("/api/lifecycle/softdelete/save", api_lifecycle_softdelete_save, methods=["POST"])
