# -*- coding: utf-8 -*-
"""handoff_routes.py — Menu "Perutean Layanan (Handoff)" untuk chatbot RAG.

Kelola tabel handoff_routing (handoff_routing_db) TANPA perlu redeploy:
tambah/ubah/hapus intent LAYANAN beserta kanal penyelesaian
(mandiri / Live Chat Agent / KPP) dan frasa pemicunya.

Perutean diterapkan otomatis saat menjawab (handoff_routing_patch); halaman ini
hanya untuk MENGELOLA datanya. Intent yang tidak terdaftar tetap dijawab RAG.

Endpoint:
  GET  /handoff              -> halaman kelola (admin)
  POST /api/handoff/list     -> daftar entri (+cari)
  POST /api/handoff/save     -> tambah/ubah entri
  POST /api/handoff/delete   -> hapus entri
  GET  /api/handoff/stats    -> ringkasan angka

Akses admin lewat _route_area di app_core (area 'peraturan').
Daftarkan: import handoff_routes; handoff_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

from handoff import routing_db as hrdb


async def _body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


async def page_handoff(request: Request):
    extra = {"n_handoff": 0, "n_handoff_total": 0}
    try:
        st = hrdb.stats()
        extra["n_handoff"] = st.get("aktif", 0)
        extra["n_handoff_total"] = st.get("total", 0)
    except Exception:
        pass
    return render_page(request, "handoff.html", "handoff", extra)


async def api_list(request: Request):
    b = await _body(request)
    try:
        rows = await run_in_threadpool(hrdb.list_all, (b.get("q") or "").strip())
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_save(request: Request):
    b = await _body(request)
    if not str(b.get("top_intent") or "").strip():
        return JSONResponse({"ok": False, "error": "Field 'top_intent' wajib diisi."})
    try:
        res = await run_in_threadpool(hrdb.upsert, b)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_delete(request: Request):
    b = await _body(request)
    idv = b.get("id")
    if not idv:
        return JSONResponse({"ok": False, "error": "Field 'id' wajib diisi."})
    try:
        res = await run_in_threadpool(hrdb.delete, idv)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_stats(request: Request):
    try:
        st = await run_in_threadpool(hrdb.stats)
        return JSONResponse({"ok": True, **st})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/handoff", page_handoff, methods=["GET"])
    app.add_api_route("/api/handoff/list", api_list, methods=["POST"])
    app.add_api_route("/api/handoff/save", api_save, methods=["POST"])
    app.add_api_route("/api/handoff/delete", api_delete, methods=["POST"])
    app.add_api_route("/api/handoff/stats", api_stats, methods=["GET"])
