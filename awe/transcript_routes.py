# -*- coding: utf-8 -*-
"""awe/transcript_routes.py — PR B: halaman transkrip livechat AWE (gelembung).

Rute GET /api/rag/agent/transkrip/{sid} merender transkrip satu percakapan AWE
sebagai gelembung (Pelanggan / Petugas / Bot), untuk dibuka di tab baru dari
kartu sumber "Percakapan AWE" di halaman Chat Baru.

Keamanan:
  * Path diawali /api/rag/agent → app_core._route_area = 'chat', jadi peran
    'agent' pun boleh (menu lain tetap tertutup). Middleware sesi tetap
    mewajibkan login (BUKAN endpoint publik).
  * PII (NIK/NPWP, telepon, email, dan nama customer) DIMASK sebelum dirender,
    memakai common.pii_mask bila tersedia; fallback regex lokal.

Didaftarkan oleh rag.awe_link_patch (import-time) via register().
"""
import html as _html
import re as _re2

from fastapi import Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

import avaya.db as avdb

try:
    import common.pii_mask as _pii
except Exception:            # pragma: no cover
    _pii = None

_registered = False

_BOT_ROLES = {"bot", "ccai", "chatbot", "virtual assistant", "google"}
_BOT_NAME_RE = _re2.compile(r"ccai|chatbot|virtual\s+assistant|google", _re2.I)

# Fallback masking bila common.pii_mask tak tersedia.
_EMAIL_RE = _re2.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_NIK_RE = _re2.compile(r"\b\d{15,16}\b")
_PHONE_RE = _re2.compile(r"\b(?:\+?62|0)8\d{7,11}\b")


def _mask(text):
    t = str(text or "")
    if _pii is not None:
        try:
            return _pii.mask_text(t)
        except Exception:
            pass
    t = _EMAIL_RE.sub("[email]", t)
    t = _NIK_RE.sub("[NIK/NPWP]", t)
    t = _PHONE_RE.sub("[telepon]", t)
    return t


def _mask_name(name):
    n = str(name or "").strip()
    if not n:
        return "Pelanggan"
    parts = n.split()
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + " ".join((p[0].upper() + ".") for p in parts[1:])


def _side(role, text):
    r = (role or "").strip().lower()
    if r in _BOT_ROLES or _BOT_NAME_RE.search(r):
        return "bot"
    try:
        if avdb._is_agent(role, text):
            return "agent"
    except Exception:
        pass
    return "customer"


_LABEL = {"agent": "Petugas", "bot": "Bot / Virtual Assistant", "customer": "Pelanggan"}


def _page(title, inner):
    return (
        "<!doctype html><html lang=\"id\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>" + _html.escape(title) + "</title><style>"
        ":root{--bg:#0f1419;--panel:#1a2028;--line:#2a323d;--muted:#8a97a6;"
        "--cust:#243447;--cust-t:#dbeafe;--agent:#1f3d2b;--agent-t:#d7f5df;"
        "--bot:#2c2733;--bot-t:#e7dcf5;}"
        "*{box-sizing:border-box}"
        "body{margin:0;background:var(--bg);color:#e6edf3;"
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5}"
        ".wrap{max-width:820px;margin:0 auto;padding:22px 16px 60px}"
        ".hdr{border:1px solid var(--line);background:var(--panel);border-radius:14px;"
        "padding:14px 16px;margin-bottom:18px}"
        ".hdr .cust{font-size:16px;font-weight:700}"
        ".hdr .meta{font-size:12.5px;color:var(--muted);margin-top:4px}"
        ".chat{display:flex;flex-direction:column;gap:10px}"
        ".row{display:flex}"
        ".row.customer{justify-content:flex-start}"
        ".row.agent{justify-content:flex-end}"
        ".row.bot{justify-content:center}"
        ".bubble{max-width:78%;border-radius:14px;padding:9px 13px;font-size:14px;"
        "border:1px solid var(--line)}"
        ".row.customer .bubble{background:var(--cust);color:var(--cust-t);border-bottom-left-radius:4px}"
        ".row.agent .bubble{background:var(--agent);color:var(--agent-t);border-bottom-right-radius:4px}"
        ".row.bot .bubble{background:var(--bot);color:var(--bot-t);max-width:90%;font-size:12.5px;opacity:.92}"
        ".who{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;"
        "opacity:.7;margin-bottom:3px}"
        ".txt{word-wrap:break-word}"
        ".empty{color:var(--muted);text-align:center;padding:40px 10px}"
        ".note{margin-top:22px;font-size:11.5px;color:var(--muted);border-top:1px solid var(--line);"
        "padding-top:12px}"
        "</style></head><body><div class=\"wrap\">" + inner + "</div></body></html>"
    )


async def awe_transcript_page(request: Request, sid: str = ""):
    sid = str(sid or "").strip()
    if not sid:
        return HTMLResponse(_page("Transkrip", "<p class='empty'>SID tidak valid.</p>"),
                            status_code=400)

    def _load():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.get_transcript(conn, sid)
        finally:
            conn.close()

    try:
        tx = await run_in_threadpool(_load)
    except Exception:
        return HTMLResponse(_page("Transkrip", "<p class='empty'>Gagal memuat transkrip.</p>"),
                            status_code=500)
    if not tx or not tx.get("transkrip"):
        return HTMLResponse(
            _page("Transkrip",
                  "<p class='empty'>Transkrip untuk percakapan ini tidak ditemukan.</p>"),
            status_code=404)

    bubbles = []
    for seg in tx.get("transkrip") or []:
        if not isinstance(seg, dict):
            continue
        text = seg.get("text", "")
        if not str(text or "").strip():
            continue
        side = _side(seg.get("role", ""), text)
        safe = _html.escape(_mask(text)).replace("\n", "<br>")
        bubbles.append(
            "<div class=\"row %s\"><div class=\"bubble\"><div class=\"who\">%s</div>"
            "<div class=\"txt\">%s</div></div></div>"
            % (side, _html.escape(_LABEL[side]), safe))

    meta = []
    if tx.get("jenis_layanan"):
        meta.append("Layanan: " + _html.escape(str(tx["jenis_layanan"])))
    if tx.get("agent_name"):
        meta.append("Petugas: " + _html.escape(_mask_name(tx.get("agent_name"))))
    meta.append("SID: " + _html.escape(sid))

    header = ("<div class=\"hdr\"><div class=\"cust\">%s</div><div class=\"meta\">%s</div></div>"
              % (_html.escape(_mask_name(tx.get("customer"))), " · ".join(meta)))
    body = header + "<div class=\"chat\">" + "".join(bubbles) + "</div>"
    body += ("<p class=\"note\">Data pribadi (NIK/NPWP, nomor telepon, email, nama) "
             "disamarkan otomatis. Transkrip ini disediakan untuk memverifikasi rujukan "
             "jawaban RAG.</p>")
    return HTMLResponse(_page("Transkrip Percakapan AWE · " + sid, body))


def register(app=None):
    """Daftarkan rute transkrip. Idempoten (hanya sekali)."""
    global _registered
    if _registered:
        return
    if app is None:
        from app_core import app as _app
        app = _app
    app.add_api_route("/api/rag/agent/transkrip/{sid}", awe_transcript_page, methods=["GET"])
    _registered = True
