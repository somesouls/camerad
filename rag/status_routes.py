# -*- coding: utf-8 -*-
"""rag_status_routes.py — Endpoint status mesin RAG (read-only, admin).

GET /api/rag/status -> ringkasan status runtime:
  * reranker cross-encoder (aktif? model? perangkat/GPU?),
  * embedding korpus peraturan (bge-m3: aktif? dimensi?),
  * indeks FTS + jumlah unit/vektor/pasal peraturan,
  * konfigurasi router (v2 + soft-prior).

Dipakai kartu \"Status Mesin & Model\" di halaman Konfigurasi RAG Agent untuk
memverifikasi apakah GPU & model benar-benar aktif (mis. mendeteksi torch
CPU-only di PC ber-GPU). READ-ONLY; tiap blok fail-soft (error pada satu blok
tak menjatuhkan seluruh endpoint). Gating admin mengikuti prefix /api/rag/ di
app_core._route_area (konsisten dengan /api/rag/profiles).

Didaftarkan oleh rag.routes.register(app) via rag_status_routes.register(app).
"""
import os

from fastapi import Request
from fastapi.responses import JSONResponse


def _b(name, default):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "off")


async def api_rag_status(request: Request):
    out = {"ok": True}

    # 1) Reranker cross-encoder ------------------------------------------------
    try:
        import rag.reranker as rr
        info = {}
        try:
            info = dict(rr.device_info() or {})
        except Exception:
            pass
        try:
            info["available"] = bool(rr.is_available())
        except Exception:
            info["available"] = False
        try:
            info["pool"] = rr._pool()
        except Exception:
            pass
        out["reranker"] = info
    except Exception as e:
        out["reranker"] = {"error": str(e)[:160]}

    # 2) Embedding korpus peraturan (bge-m3) -----------------------------------
    try:
        import peraturan.semantic as psem
        emb = {}
        try:
            emb["enabled"] = bool(psem._enabled())
        except Exception:
            pass
        try:
            emb["model"] = psem.model_id()
        except Exception:
            pass
        try:
            emb["available"] = bool(psem.is_available())
        except Exception:
            emb["available"] = False
        try:
            emb["dim"] = int(psem.embed_dim() or 0)
        except Exception:
            pass
        try:
            emb["query_prefix"] = psem.query_prefix()
            emb["passage_prefix"] = psem.passage_prefix()
        except Exception:
            pass
        out["embedding"] = emb
    except Exception as e:
        out["embedding"] = {"error": str(e)[:160]}

    # 3) Indeks FTS + jumlah unit/vektor/pasal peraturan -----------------------
    try:
        import peraturan.db as pdb
        per = {}
        try:
            per = dict(pdb.fts_info() or {})
        except Exception:
            pass
        try:
            st = pdb.stats() or {}
            per["total_unit"] = st.get("total_unit")
            per["total_vec"] = st.get("total_vec")
            per["total_pasal"] = st.get("total_pasal")
        except Exception:
            pass
        out["peraturan"] = per
    except Exception as e:
        out["peraturan"] = {"error": str(e)[:160]}

    # 4) Konfigurasi router ----------------------------------------------------
    out["router"] = {
        "router2": _b("RAG_ROUTER2", "1"),
        "softprior": _b("RAG_ROUTER_SOFTPRIOR", "1"),
        "min_reg": os.environ.get("RAG_ROUTER2_MIN_REG", "2"),
        "skip": os.environ.get("RAG_ROUTER2_SKIP", "intent,awe"),
    }
    return JSONResponse(out)


def register(app):
    app.add_api_route("/api/rag/status", api_rag_status, methods=["GET"])
