# -*- coding: utf-8 -*-
"""jobs_routes.py — Fase 4: endpoint ASINKRON untuk jalur AGENTIC.

Menyediakan:
  - POST /api/ask-agentic/start            -> mulai job, balas {job_id} cepat.
  - GET  /api/ask-agentic/status/{job_id}  -> polling status/hasil.
  - POST /api/ask-agentic/cancel/{job_id}  -> batalkan job.

Body /start sama dengan /api/ask-agentic (question, page?, lang?, max_iters?).
Scope presisi per-halaman (ASK_AGENTIC_SCOPES) dipakai ulang dari
knowledge.routes agar perilaku identik dengan endpoint sinkron.

SIFAT: ADITIF & NON-BREAKING. Endpoint sinkron /api/ask-agentic tetap ada.
Autentikasi & otorisasi ditangani middleware app_core (area 'common', action
'read'), sama seperti /api/ask (path /api/ask-agentic/* diawali '/api/ask').

Fase 5: sebelum job dibuat, permintaan melewati RATE LIMIT per pengguna
(knowledge.guardrails). Identitas pengguna (dari request.state.user yang
diset middleware) diteruskan sebagai created_by untuk keperluan audit.
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from knowledge import agentic as agentic
from knowledge import jobs as agentic_jobs
from knowledge import guardrails as guardrails
from knowledge.routes import ASK_AGENTIC_SCOPES


def _agentic_input(page, question):
    """Bungkus pertanyaan dgn konteks scope halaman (Tahap 3) bila terdaftar."""
    scope = ASK_AGENTIC_SCOPES.get((page or "").strip().lower())
    if scope:
        return ("[KONTEKS HALAMAN untuk mengarahkan penelusuran]\n" + scope +
                "\n\n[PERTANYAAN PENGGUNA]\n" + question)
    return question


def _user_id(request):
    """Identitas pengguna dari middleware app_core (untuk audit & rate-limit)."""
    try:
        u = getattr(request.state, "user", None)
    except Exception:
        u = None
    if isinstance(u, dict):
        return (u.get("username") or u.get("nama") or "").strip()
    return ""


async def api_ask_agentic_start(request: Request):
    """Mulai job agentic ASINKRON; balas {ok, job_id, status:'queued'} cepat."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    lang = body.get("lang") or None
    page = (body.get("page") or "").strip().lower()
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        max_iters = int(body.get("max_iters") or agentic.MAX_ITERS)
    except Exception:
        max_iters = agentic.MAX_ITERS
    max_iters = max(1, min(max_iters, agentic.MAX_ITERS))

    created_by = _user_id(request)
    # Fase 5: rate limit per pengguna (best-effort; kegagalan -> lolos).
    try:
        rl = guardrails.check_rate_limit(created_by)
    except Exception:
        rl = {"ok": True}
    if not rl.get("ok"):
        return JSONResponse({"ok": False, "rate_limited": True,
                             "retry_after": rl.get("retry_after"),
                             "error": rl.get("error") or
                             "Terlalu banyak permintaan. Coba lagi sebentar."},
                            status_code=429)

    q_in = _agentic_input(page, question)
    try:
        job = agentic_jobs.start_job(q_in, lang=lang, max_iters=max_iters,
                                     question=question, page=page,
                                     created_by=created_by)
        return JSONResponse({"ok": True, "job_id": job["job_id"],
                             "status": job["status"], "mode": "agentic"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_ask_agentic_status(request: Request):
    """Status job agentic. Saat 'done' menyertakan answer/steps/databases."""
    job_id = request.path_params.get("job_id") or ""
    try:
        job = agentic_jobs.get_job(job_id, with_result=True)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "status": "error"})
    if not job:
        return JSONResponse({"ok": False, "status": "unknown",
                             "error": "job tidak ditemukan."})
    status = job.get("status")
    resp = {"ok": True, "status": status, "job_id": job_id,
            "steps_done": job.get("steps_done", 0)}
    if status == "done":
        res = job.get("result") or {}
        resp.update({
            "mode": "agentic",
            "answer": res.get("answer", ""),
            "databases": res.get("databases", []),
            "steps": res.get("steps", []),
            "note": res.get("note"),
        })
    elif status == "error":
        resp["ok"] = False
        resp["error"] = job.get("error") or "Job gagal diproses."
    return JSONResponse(resp)


async def api_ask_agentic_cancel(request: Request):
    """Batalkan job agentic (queued: penuh; running: hasil dibuang saat selesai)."""
    job_id = request.path_params.get("job_id") or ""
    try:
        return JSONResponse(agentic_jobs.cancel_job(job_id))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/api/ask-agentic/start", api_ask_agentic_start,
                      methods=["POST"])
    app.add_api_route("/api/ask-agentic/status/{job_id}",
                      api_ask_agentic_status, methods=["GET"])
    app.add_api_route("/api/ask-agentic/cancel/{job_id}",
                      api_ask_agentic_cancel, methods=["POST"])
