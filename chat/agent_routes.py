# -*- coding: utf-8 -*-
"""
agent_chat_routes.py — Chat RAG "Agent Kring Pajak" (halaman utama "/").

Semua peran memakai chat ini sebagai satu-satunya kanal tanya-AI: pertanyaan
di-grounding ke basis pengetahuan perpajakan (profil 'agent'); jawaban
disertai tautan sumber (peraturan source_url + permalink X/Twitter) dan tombol
feedback jempol (wajib diklik oleh agent). Pertanyaan umum non-perpajakan
otomatis ditolak lewat kalimat fallback profil (chat umum sudah dihapus).

PR C (conversation_id): tiap interaksi ditautkan ke satu percakapan (conv_id)
yang disimpan permanen (db.agent_log_db.rag_conversation). Bila frontend belum
mengirim conv_id, id diturunkan secara deterministik dari (username +
pertanyaan pertama pada history) sehingga turn-turn satu sesi tetap
terkelompok tanpa perubahan frontend. conv_id dikembalikan pada respons.

Endpoint:
  POST /api/rag/agent                    -> jawab (grounded), catat log, balikan log_id + conv_id + sumber
  POST /api/rag/feedback                 -> simpan jempol naik/turun untuk sebuah jawaban
  GET  /api/rag/quota                    -> (admin) lihat kuota harian agent & chatbot
  POST /api/rag/quota/save               -> (admin) setel kuota harian
  GET  /api/rag/agent/conversations      -> daftar percakapan milik pengguna (JSON)
  GET  /api/rag/agent/percakapan/{conv_id} -> transkrip percakapan (HTML bubble, owner/admin)
  GET  /rag-agent                        -> (admin) halaman konfigurasi mesin RAG profil agent
  GET  /rag-chatbot                      -> (admin) halaman konfigurasi mesin RAG profil chatbot
  GET  /api/rag/logs                     -> (admin) daftar log chat + feedback untuk review
"""
import html as _html
import hashlib

from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag.engine as rag_engine
import rag.config_db as rcfg
import db.agent_log_db as aldb


async def _body(request):
    try:
        return await request.json()
    except Exception:
        return {}


def _user(request):
    u = getattr(request.state, "user", None)
    return u if isinstance(u, dict) else {}


def _first_user_text(history, fallback=""):
    """Ambil teks pertanyaan pengguna PERTAMA dari history (toleran beragam
    bentuk item: {role,content}/{q,a}/str). Dipakai utk judul + turunan conv_id.
    """
    if isinstance(history, list):
        for m in history:
            if isinstance(m, dict):
                role = str(m.get("role") or m.get("who") or m.get("from") or "").strip().lower()
                txt = (m.get("content") or m.get("text") or m.get("q")
                       or m.get("question") or m.get("message") or "")
                if role in ("", "user", "human", "wp") and str(txt).strip():
                    return str(txt).strip()
            elif isinstance(m, str) and m.strip():
                return m.strip()
    return (fallback or "").strip()


def _derive_conv_id(username, question, history):
    """conv_id deterministik: sama untuk semua turn dgn pertanyaan pertama sama.
    Catatan: dua sesi berbeda yang KEBETULAN diawali pertanyaan identik dari
    user yang sama akan tergabung; frontend disarankan mengirim conv_id eksplisit
    (mis. id chat di localStorage) untuk pemisahan yang tepat.
    """
    seed = _first_user_text(history, question) or (question or "")
    base = (username or "anon") + "\n" + seed
    return "c_" + hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:20]


def _conv_page(title, inner):
    return (
        "<!doctype html><html lang=\"id\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>" + _html.escape(title) + "</title><style>"
        ":root{--bg:#0f1419;--panel:#1a2028;--line:#2a323d;--muted:#8a97a6;"
        "--me:#243447;--me-t:#dbeafe;--ai:#1f2b24;--ai-t:#d7f5df;}"
        "*{box-sizing:border-box}"
        "body{margin:0;background:var(--bg);color:#e6edf3;"
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5}"
        ".wrap{max-width:820px;margin:0 auto;padding:22px 16px 60px}"
        ".hdr{border:1px solid var(--line);background:var(--panel);border-radius:14px;"
        "padding:14px 16px;margin-bottom:18px}"
        ".hdr .ttl{font-size:16px;font-weight:700}"
        ".hdr .meta{font-size:12.5px;color:var(--muted);margin-top:4px}"
        ".chat{display:flex;flex-direction:column;gap:12px}"
        ".row{display:flex}.row.me{justify-content:flex-end}.row.ai{justify-content:flex-start}"
        ".bubble{max-width:80%;border-radius:14px;padding:9px 13px;font-size:14px;border:1px solid var(--line)}"
        ".row.me .bubble{background:var(--me);color:var(--me-t);border-bottom-right-radius:4px}"
        ".row.ai .bubble{background:var(--ai);color:var(--ai-t);border-bottom-left-radius:4px}"
        ".who{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;opacity:.7;margin-bottom:3px}"
        ".txt{word-wrap:break-word;white-space:pre-wrap}"
        ".src{margin-top:7px;padding-top:6px;border-top:1px dashed var(--line);font-size:12px}"
        ".src a{color:#7cc0ff;text-decoration:none}.src a:hover{text-decoration:underline}"
        ".src .nourl{color:var(--muted)}"
        ".empty{color:var(--muted);text-align:center;padding:40px 10px}"
        "</style></head><body><div class=\"wrap\">" + inner + "</div></body></html>"
    )


def _src_links_html(sources):
    items = []
    for s in (sources or []):
        if not isinstance(s, dict):
            continue
        label = _html.escape(str(s.get("judul") or s.get("sumber") or "Sumber"))
        url = str(s.get("url") or "").strip()
        if url:
            items.append("<a href=\"%s\" target=\"_blank\" rel=\"noopener\">%s</a>"
                         % (_html.escape(url), label))
        else:
            items.append("<span class=\"nourl\">%s</span>" % label)
    if not items:
        return ""
    return "<div class=\"src\">Sumber: " + " · ".join(items) + "</div>"


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

        # PR C: id percakapan — pakai kiriman frontend bila ada, jika tidak
        # turunkan deterministik dari (user + pertanyaan pertama).
        conv_id = (body.get("conv_id") or body.get("conversation_id") or "").strip()
        if not conv_id:
            conv_id = _derive_conv_id(username, question, history)

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

        # PR C: catat percakapan (judul = pertanyaan pertama) lalu log turn.
        try:
            aldb.upsert_conversation(conv_id, username, "agent",
                                     title=_first_user_text(history, question))
        except Exception:
            pass

        log_id = aldb.log_chat(username, role, "agent", question, answer,
                               sources, grounded, domain, conv_id=conv_id)

        out = {"ok": True, "answer": answer, "sources": sources,
               "grounded": grounded, "domain": domain, "log_id": log_id,
               "conv_id": conv_id}
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

    async def api_rag_conversations(request: Request):
        u = _user(request)
        username = (u.get("username") or "").strip()
        qp = request.query_params
        try:
            limit = int(qp.get("limit") or 100)
        except Exception:
            limit = 100
        convos = aldb.list_conversations(username, "agent", limit)
        return JSONResponse({"ok": True, "conversations": convos})

    async def page_rag_percakapan(request: Request, conv_id: str = ""):
        u = _user(request)
        username = (u.get("username") or "").strip()
        role = (u.get("role") or "").strip().lower()
        conv_id = (conv_id or "").strip()
        conv = aldb.get_conversation(conv_id)
        if not conv:
            return HTMLResponse(_conv_page("Percakapan",
                "<p class='empty'>Percakapan tidak ditemukan.</p>"), status_code=404)
        owner = (conv.get("username") or "").strip()
        if role != "admin" and owner and owner != username:
            return HTMLResponse(_conv_page("Percakapan",
                "<p class='empty'>Anda tidak berhak melihat percakapan ini.</p>"),
                status_code=403)
        msgs = aldb.get_conversation_messages(conv_id)
        rows = []
        for m in msgs:
            q = _html.escape(str(m.get("question") or "")).replace("\n", "<br>")
            a = _html.escape(str(m.get("answer") or "")).replace("\n", "<br>")
            if q:
                rows.append("<div class=\"row me\"><div class=\"bubble\">"
                            "<div class=\"who\">Anda</div><div class=\"txt\">%s</div></div></div>" % q)
            if a:
                rows.append("<div class=\"row ai\"><div class=\"bubble\">"
                            "<div class=\"who\">Agent Kring Pajak</div>"
                            "<div class=\"txt\">%s</div>%s</div></div>"
                            % (a, _src_links_html(m.get("sources"))))
        header = ("<div class=\"hdr\"><div class=\"ttl\">%s</div>"
                  "<div class=\"meta\">%d giliran · mulai %s</div></div>"
                  % (_html.escape(str(conv.get("title") or "Percakapan")),
                     int(conv.get("turns") or len(msgs)),
                     _html.escape(str(conv.get("created_at") or ""))))
        if rows:
            body_html = header + "<div class=\"chat\">" + "".join(rows) + "</div>"
        else:
            body_html = header + "<p class='empty'>Belum ada pesan.</p>"
        return HTMLResponse(_conv_page(
            "Percakapan · " + str(conv.get("title") or conv_id), body_html))

    async def page_rag_agent(request: Request):
        return render_page(request, "rag_agent.html", "rag_agent", {
            "sumber_valid": list(rcfg.SUMBER_VALID),
            "sumber_label": rcfg.SUMBER_LABEL,
        })

    async def page_rag_chatbot(request: Request):
        return render_page(request, "rag_chatbot.html", "rag_chatbot", {
            "sumber_valid": list(rcfg.SUMBER_VALID),
            "sumber_label": rcfg.SUMBER_LABEL,
        })

    async def api_rag_logs(request: Request):
        qp = request.query_params

        def _int(v, dv):
            try:
                return int(v)
            except Exception:
                return dv

        res = aldb.list_logs(
            username=qp.get("q") or qp.get("username") or "",
            feedback=qp.get("feedback") or "",
            grounded=qp.get("grounded") or "",
            domain=qp.get("domain") or "",
            profil=qp.get("profil") or "agent",
            range_=qp.get("range") or "all",
            start=qp.get("start") or "",
            end=qp.get("end") or "",
            limit=_int(qp.get("limit"), 300),
        )
        return JSONResponse(res)

    app.add_api_route("/api/rag/agent", api_rag_agent, methods=["POST"])
    app.add_api_route("/api/rag/feedback", api_rag_feedback, methods=["POST"])
    app.add_api_route("/api/rag/quota", api_rag_quota, methods=["GET"])
    app.add_api_route("/api/rag/quota/save", api_rag_quota_save, methods=["POST"])
    app.add_api_route("/api/rag/agent/conversations", api_rag_conversations, methods=["GET"])
    app.add_api_route("/api/rag/agent/percakapan/{conv_id}", page_rag_percakapan, methods=["GET"])
    app.add_api_route("/rag-agent", page_rag_agent, methods=["GET"])
    app.add_api_route("/rag-chatbot", page_rag_chatbot, methods=["GET"])
    app.add_api_route("/api/rag/logs", api_rag_logs, methods=["GET"])
