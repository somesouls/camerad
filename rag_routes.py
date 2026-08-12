# -*- coding: utf-8 -*-
"""rag_routes.py — Halaman & API mesin RAG (chat produksi + playground admin).

Rute:
  GET  /rag                  -> ChatBot Pajak untuk Wajib Pajak (profil 'chatbot')
  POST /api/rag/chat         -> jawab chat produksi
  GET  /rag-lab              -> Playground admin: uji kombinasi sumber & profil
  POST /api/rag/lab          -> jalankan uji (pilih profil + centang sumber + diagnostik)
  GET  /api/rag/profiles     -> daftar profil (admin)
  POST /api/rag/profile      -> ambil satu profil {id} (admin)
  POST /api/rag/profile/save -> simpan profil/prompt (admin)

Mesin inti ada di rag_engine.py; profil/prompt/chip di rag_config_db.py.
Gating akses diatur di app_core (_route_area): /rag & /api/rag/chat = area
'common' (semua pengguna login), sisanya area 'peraturan' (admin).

Daftarkan dengan:  import rag_routes; rag_routes.register(app)
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag_engine
import rag_config_db as rcfg


async def _body(request: Request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Halaman
# --------------------------------------------------------------------------
async def page_rag(request: Request):
    return render_page(request, "rag.html", "rag")


async def page_rag_lab(request: Request):
    return render_page(request, "rag_lab.html", "rag_lab", {
        "sumber_valid": list(rcfg.SUMBER_VALID),
        "sumber_label": rcfg.SUMBER_LABEL,
    })


# --------------------------------------------------------------------------
# API chat produksi
# --------------------------------------------------------------------------
async def api_rag_chat(request: Request):
    body = await _body(request)
    question = (body.get("question") or "").strip()
    history = body.get("history") if isinstance(body.get("history"), list) else []
    if not question and history:
        for h in reversed(history):
            if isinstance(h, dict) and (h.get("role") or "").lower() == "user":
                question = (h.get("content") or "").strip()
                break
    if not question:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    try:
        res = await run_in_threadpool(rag_engine.jawab_chat, question, history, "chatbot")
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# --------------------------------------------------------------------------
# API playground admin
# --------------------------------------------------------------------------
async def api_rag_lab(request: Request):
    body = await _body(request)
    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    profil = (body.get("profil") or "chatbot").strip()
    sumber = body.get("sumber")
    if not isinstance(sumber, list):
        sumber = None
    history = body.get("history") if isinstance(body.get("history"), list) else []
    try:
        res = await run_in_threadpool(rag_engine.jawab_lab, question, profil, sumber, history)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_profiles(request: Request):
    try:
        return JSONResponse({
            "ok": True,
            "profil": rcfg.list_profiles(),
            "sumber_valid": list(rcfg.SUMBER_VALID),
            "sumber_label": rcfg.SUMBER_LABEL,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_profile_get(request: Request):
    body = await _body(request)
    pid = (body.get("id") or "").strip()
    p = rcfg.get_profile(pid) if pid else None
    if not p:
        return JSONResponse({"ok": False, "error": "Profil tak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "profil": p})


async def api_profile_save(request: Request):
    body = await _body(request)
    pid = (body.get("id") or "").strip()
    if not pid:
        return JSONResponse({"ok": False, "error": "id profil wajib."}, status_code=400)
    try:
        p = await run_in_threadpool(rcfg.save_profile, pid, body)
        return JSONResponse({"ok": True, "profil": p})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def register(app):
    app.add_api_route("/rag", page_rag, methods=["GET"])
    app.add_api_route("/api/rag/chat", api_rag_chat, methods=["POST"])
    app.add_api_route("/rag-lab", page_rag_lab, methods=["GET"])
    app.add_api_route("/api/rag/lab", api_rag_lab, methods=["POST"])
    app.add_api_route("/api/rag/profiles", api_profiles, methods=["GET"])
    app.add_api_route("/api/rag/profile", api_profile_get, methods=["POST"])
    app.add_api_route("/api/rag/profile/save", api_profile_save, methods=["POST"])
