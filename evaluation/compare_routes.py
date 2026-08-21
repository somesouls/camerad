# -*- coding: utf-8 -*-
"""evaluation/compare_routes.py — Menu web \"RAG vs LoRA\" (deliverable #3).

Halaman untuk membandingkan efektivitas & keandalan RAG (retrieval) vs LoRA
(fine-tuning) berdampingan, memakai evaluation.compare (3 mode per pertanyaan:
rag_base / lora / lora_rag). Generasi lewat server lokal finetune.serve_local.

Rute (area akses 'peraturan' = admin):
  GET  /rag-vs-lora        -> halaman
  POST /api/compare/run    -> jalankan perbandingan 1 pertanyaan (3 mode)

Daftarkan: import evaluation.compare_routes as m; m.register(app)
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag.config_db as rcfg
import evaluation.compare as ec


async def _body(request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def page(request: Request):
    try:
        profils = rcfg.list_profiles()
    except Exception:
        profils = []
    return render_page(request, "compare.html", "rag_compare",
                       {"profil_list": profils})


async def api_run(request: Request):
    b = await _body(request)
    q = (b.get("question") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "pertanyaan kosong"})
    adapter = (b.get("adapter") or "camerad-grounded").strip()
    profil = (b.get("profil") or "chatbot").strip()
    judge = bool(b.get("judge", False))
    try:
        temperature = float(b.get("temperature", 0.3))
    except Exception:
        temperature = 0.3
    try:
        max_tokens = int(b.get("max_tokens", 512))
    except Exception:
        max_tokens = 512
    try:
        res = await run_in_threadpool(
            ec.compare_one, q, adapter, None, profil, judge,
            temperature, max_tokens)
        return JSONResponse({"ok": True, "hasil": res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]})


def register(app):
    app.add_api_route("/rag-vs-lora", page, methods=["GET"])
    app.add_api_route("/api/compare/run", api_run, methods=["POST"])
