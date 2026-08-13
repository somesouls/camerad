# -*- coding: utf-8 -*-
"""
agent_chat_routes.py — Chat RAG "Agent Kring Pajak" (halaman utama "/").

Semua peran memakai chat ini sebagai satu-satunya kanal tanya-AI: pertanyaan
di-grounding ke basis pengetahuan perpajakan (profil 'agent'); jawaban
disertai tautan sumber (peraturan source_url + permalink X/Twitter) dan tombol
feedback jempol (wajib diklik oleh agent). Pertanyaan umum non-perpajakan
otomatis ditolak lewat kalimat fallback profil (chat umum sudah dihapus).

Endpoint:
  POST /api/rag/agent      -> jawab (grounded), catat log, balikan log_id + sumber
  POST /api/rag/feedback   -> simpan jempol naik/turun untuk sebuah jawaban
  GET  /api/rag/quota      -> (admin) lihat kuota harian agent & chatbot
  POST /api/rag/quota/save -> (admin) setel kuota harian
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import rag_engine
import agent_log_db as aldb


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return {}


def _user(request):
    u = getattr(request.state, "user", None)
    return u if isinstance(u, dict) else {}


def register(app):

    async def api_rag_agent(request: Request):
        u = _user(request)
        username = (u.get("username") or "").strip()
        role = (u.get("role") or "").strip()
        body = await _body(request)
        question = (body.get("question") or "").strip()
        history = body.get("history") or []
        if not question:
            return JSONResponse({"ok": False, "error": "Pertanyaan kosong."}, status_code=400)
        if not isinstance(history, list):
            history = []

        # Kuota harian: berlaku untuk peran 'agent' (target kuota 'agent').
        if role == "agent":
            q = aldb.get_quota("agent")
            limit = int(q.get("maks_tanya") or 0)
            if limit > 0:
                used = aldb.count_today(username)
                if used >= limit:
                    return JSONResponse({
                        "ok": False, "limit": True,
                        "error": ("Kuota pertanyaan harian Anda (%d) sudah habis. "
                                  "Silakan coba lagi besok atau hubungi admin." % limit),
                        "quota": {"used": used, "limit": limit},
                    }, status_code=429)

        try:
            res = await run_in_threadpool(rag_engine.jawab_chat, question, history, "agent")
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        if not (res and res.get("ok")):
            return JSONResponse(
                {"ok": False, "error": (res or {}).get("error") or "Gagal menjawab."},
                status_code=500,
            )

        answer = res.get("answer") or ""
        sources = res.get("sources") or []
        grounded = bool(res.get("grounded"))
        domain = res.get("domain") or ""

        log_id = aldb.log_chat(username, role, "agent", question, answer,
                               sources, grounded, domain)

        out = {"ok": True, "answer": answer, "sources": sources,
               "grounded": grounded, "domain": domain, "log_id": log_id}
        if role == "agent":
            q = aldb.get_quota("agent")
            out["quota"] = {"used": aldb.count_today(username),
                            "limit": int(q.get("maks_tanya") or 0)}
        return JSONResponse(out)

    async def api_rag_feedback(request: Request):
        u = _user(request)
        username = (u.get("username") or "").strip()
        body = await _body(request)
        log_id = body.get("log_id")
        rating = body.get("rating") or body.get("feedback")
        if log_id is None:
            return JSONResponse({"ok": False, "error": "log_id wajib."}, status_code=400)
        try:
            log_id = int(log_id)
        except Exception:
            return JSONResponse({"ok": False, "error": "log_id tidak valid."}, status_code=400)
        res = aldb.set_feedback(log_id, rating, username=username)
        return JSONResponse(res, status_code=(200 if res.get("ok") else 400))

    async def api_rag_quota(request: Request):
        return JSONResponse({"ok": True, "quota": aldb.list_quota()})

    async def api_rag_quota_save(request: Request):
        u = _user(request)
        username = (u.get("username") or "").strip()
        body = await _body(request)
        target = (body.get("target") or "").strip().lower()
        res = aldb.set_quota(target,
                             maks_tanya=body.get("maks_tanya"),
                             maks_token=body.get("maks_token"),
                             updated_by=username)
        return JSONResponse(res, status_code=(200 if res.get("ok") else 400))

    app.add_api_route("/api/rag/agent", api_rag_agent, methods=["POST"])
    app.add_api_route("/api/rag/feedback", api_rag_feedback, methods=["POST"])
    app.add_api_route("/api/rag/quota", api_rag_quota, methods=["GET"])
    app.add_api_route("/api/rag/quota/save", api_rag_quota_save, methods=["POST"])
