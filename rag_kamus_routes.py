# -*- coding: utf-8 -*-
"""rag_kamus_routes.py — Menu "Kamus & Rewriting" (Tahap 5).

Kelola kamus sinonim/istilah pajak (rag_kamus_db) yang dipakai query rewriting,
dan sediakan alat UJI "rewriting otomatis AI" untuk peraturan/pasal terkait
(rag_rewrite) beserta pratinjau hasil retrieval peraturan (dengan skor rerank).

Endpoint:
  GET  /kamus                 -> halaman kelola + uji rewriting (admin)
  POST /api/kamus/list        -> daftar entri kamus (+cari)
  POST /api/kamus/save        -> tambah/ubah entri
  POST /api/kamus/delete      -> hapus entri
  POST /api/kamus/rewrite     -> uji: perluas + rewrite AI + pratinjau retrieval
  GET  /api/kamus/stats       -> ringkasan angka + status model

Akses admin lewat _route_area di app_core (area 'peraturan').
Daftarkan: import rag_kamus_routes; rag_kamus_routes.register(app)
"""
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag_kamus_db as kdb

try:
    import rag_rewrite as rw
except Exception:            # pragma: no cover
    rw = None
try:
    import rag_reranker as rr
except Exception:            # pragma: no cover
    rr = None
try:
    import peraturan_db as pdb
except Exception:            # pragma: no cover
    pdb = None


async def _body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


async def page_kamus(request: Request):
    extra = {"rerank_aktif": False, "rewrite_ai": True, "rerank_model": "", "n_kamus": 0}
    try:
        extra["rerank_aktif"] = bool(rr and rr.is_available())
        extra["rerank_model"] = (rr.model_id() if rr else "")
    except Exception:
        pass
    try:
        extra["rewrite_ai"] = str(os.environ.get("RAG_REWRITE_AI", "1")).lower() not in ("0", "false", "no", "off")
    except Exception:
        pass
    try:
        extra["n_kamus"] = kdb.stats().get("aktif", 0)
    except Exception:
        pass
    return render_page(request, "kamus.html", "kamus", extra)


async def api_list(request: Request):
    b = await _body(request)
    try:
        rows = await run_in_threadpool(
            kdb.list_all, (b.get("q") or "").strip(),
            int(b.get("limit") or 500), int(b.get("offset") or 0))
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_save(request: Request):
    b = await _body(request)
    if not str(b.get("istilah") or "").strip():
        return JSONResponse({"ok": False, "error": "Field 'istilah' wajib diisi."})
    try:
        res = await run_in_threadpool(kdb.upsert, b)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_delete(request: Request):
    b = await _body(request)
    idv = b.get("id")
    if not idv:
        return JSONResponse({"ok": False, "error": "Field 'id' wajib diisi."})
    try:
        res = await run_in_threadpool(kdb.delete, idv)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def _uji_rewrite(q):
    hasil = {"q": q, "kamus": [], "ai": {}, "query_efektif": q, "peraturan": []}
    if rw is not None:
        try:
            hasil["kamus"] = rw.expand_kamus(q)
        except Exception:
            hasil["kamus"] = []
        try:
            hasil["ai"] = rw.rewrite_ai(q, force=True)
        except Exception as e:
            hasil["ai"] = {"dipakai": False, "alasan": "gagal: " + str(e)[:120]}
        try:
            hasil["query_efektif"] = rw.untuk_retrieval(q)
        except Exception:
            hasil["query_efektif"] = q
    if pdb is not None:
        try:
            rows = pdb.search(q, 5, ("berlaku",))
            for r in rows:
                d = r if isinstance(r, dict) else dict(r)
                jenis = str(d.get("jenis_peraturan") or "").strip()
                nomor = str(d.get("nomor") or "").strip()
                tahun = str(d.get("tahun") or "").strip()
                pasal = str(d.get("pasal") or "").strip()
                head = " ".join(x for x in [jenis, nomor, ("Tahun " + tahun) if tahun else ""] if x).strip()
                head = head or str(d.get("judul") or "Peraturan")
                if pasal:
                    head += " - Pasal " + pasal
                hasil["peraturan"].append({
                    "judul": head,
                    "status": str(d.get("status") or ""),
                    "skor": d.get("skor"),
                    "rerank_skor": d.get("rerank_skor"),
                    "cos": d.get("cos"),
                })
        except Exception as e:
            hasil["peraturan_error"] = str(e)[:160]
    return hasil


async def api_rewrite(request: Request):
    b = await _body(request)
    q = (b.get("q") or b.get("query") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    try:
        res = await run_in_threadpool(_uji_rewrite, q)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_stats(request: Request):
    try:
        st = await run_in_threadpool(kdb.stats)
        st["rerank_aktif"] = bool(rr and rr.is_available())
        st["rerank_model"] = (rr.model_id() if rr else "")
        return JSONResponse({"ok": True, **st})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/kamus", page_kamus, methods=["GET"])
    app.add_api_route("/api/kamus/list", api_list, methods=["POST"])
    app.add_api_route("/api/kamus/save", api_save, methods=["POST"])
    app.add_api_route("/api/kamus/delete", api_delete, methods=["POST"])
    app.add_api_route("/api/kamus/rewrite", api_rewrite, methods=["POST"])
    app.add_api_route("/api/kamus/stats", api_stats, methods=["GET"])
