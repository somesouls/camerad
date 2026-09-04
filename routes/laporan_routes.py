# -*- coding: utf-8 -*-
"""laporan_routes.py — Menu Laporan (Opsi B / Fase 3).

Ruang kerja laporan internal: pengguna menulis permintaan, AI *agentic*
(knowledge.agentic) menelusuri database internal (READ-ONLY, kecuali `users`)
lalu menyusun laporan Markdown. Laporan bisa DISIMPAN (reports.db), dibuka lagi,
dan DIEKSPOR (Markdown + PDF).

SIFAT: ADITIF & NON-BREAKING — modul/route/DB/template baru; endpoint lama tak
tersentuh. Satu-satunya TULIS = ke reports.db; database sumber tetap read-only.

Ekspor PDF memakai tampilan cetak browser (Simpan sebagai PDF) agar tanpa
dependency baru; ekspor Markdown mengunduh berkas .md.

Daftarkan dengan:
    import routes.laporan_routes as laporan_routes; laporan_routes.register(app)
"""
import re
import datetime as _dt

from fastapi import Request
from fastapi.responses import JSONResponse, Response, HTMLResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page
import db.reports_db as reports_db
from knowledge import agentic


def _now_jkt():
    # Waktu lokal server (mesin on-prem = Asia/Jakarta).
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _user(request):
    u = getattr(request.state, "user", None)
    if isinstance(u, dict):
        return u.get("nama") or u.get("username") or ""
    return ""


def _derive_title(question):
    q = re.sub(r"\s+", " ", (question or "").strip())
    if not q:
        return "Laporan " + _now_jkt()
    return (q[:70] + "\u2026") if len(q) > 70 else q


def _build_report_md(title, question, answer, databases, steps):
    dbs = ", ".join(databases or []) or "-"
    lines = [
        "# " + (title or "Laporan"),
        "",
        "_Disusun: %s • Sumber data: %s_" % (_now_jkt(), dbs),
        "",
        "**Permintaan:** " + (question or ""),
        "",
        (answer or "").strip(),
    ]
    q_steps = [s for s in (steps or []) if s.get("type") == "query"]
    if q_steps:
        lines += ["", "---", "", "## Lampiran — Query SQL", ""]
        for i, s in enumerate(q_steps, 1):
            status = "ok" if s.get("ok") else ("gagal: " + str(s.get("error") or ""))
            lines += [
                "**%d. %s** (%s)" % (i, s.get("db", "?"), status),
                "",
                "```sql",
                (s.get("sql") or "").strip(),
                "```",
                "",
            ]
    return "\n".join(lines)


# ---------------- API: susun laporan (agentic) ----------------
def _generate(question, title, lang):
    res = agentic.answer_agentic(question, lang)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "Gagal menyusun laporan.")}
    answer = res.get("answer") or ""
    databases = res.get("databases") or []
    steps = res.get("steps") or []
    ttl = (title or "").strip() or _derive_title(question)
    content_md = _build_report_md(ttl, question, answer, databases, steps)
    return {"ok": True, "title": ttl, "answer": answer, "content_md": content_md,
            "databases": databases, "steps": steps, "note": res.get("note")}


async def api_laporan_generate(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    title = (body.get("title") or "").strip()
    lang = body.get("lang") or None
    if not question:
        return JSONResponse({"ok": False, "error": "Permintaan laporan kosong."})
    try:
        return JSONResponse(await run_in_threadpool(_generate, question, title, lang))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ---------------- API: simpan / daftar / buka / hapus ----------------
async def api_laporan_save(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    title = (body.get("title") or "").strip()
    content_md = (body.get("content_md") or "").strip()
    question = (body.get("question") or "").strip()
    databases = body.get("databases") or []
    steps = body.get("steps") or []
    if not content_md:
        return JSONResponse({"ok": False, "error": "Isi laporan kosong; susun laporan dulu."})
    if not title:
        title = _derive_title(question)
    who = _user(request)

    def _run():
        conn = reports_db.init_db(reports_db.connect())
        try:
            rid = reports_db.create_report(
                conn, title=title, content_md=content_md, question=question,
                databases=databases, steps=steps, created_by=who)
            return {"ok": True, "id": rid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_laporan_list(request: Request):
    q = (request.query_params.get("q") or "").strip() or None

    def _run():
        conn = reports_db.init_db(reports_db.connect())
        try:
            return {"ok": True, "items": reports_db.list_reports(conn, q=q)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_laporan_get(request: Request):
    rid = request.query_params.get("id")

    def _run():
        conn = reports_db.init_db(reports_db.connect())
        try:
            return reports_db.get_report(conn, rid)
        finally:
            conn.close()
    try:
        rep = await run_in_threadpool(_run)
        if not rep:
            return JSONResponse({"ok": False, "error": "Laporan tidak ditemukan."})
        return JSONResponse({"ok": True, "report": rep})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_laporan_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    rid = (body or {}).get("id") if isinstance(body, dict) else None

    def _run():
        conn = reports_db.init_db(reports_db.connect())
        try:
            return reports_db.delete_report(conn, rid)
        finally:
            conn.close()
    try:
        ok = await run_in_threadpool(_run)
        return JSONResponse({"ok": bool(ok)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ---------------- Ekspor: Markdown (unduh) & PDF (tampilan cetak) -------------
def _safe_filename(name):
    base = re.sub(r"[^A-Za-z0-9 _\-]", "", (name or "laporan")).strip() or "laporan"
    return base[:80]


def _md_to_html(md):
    """Konversi Markdown ringkas -> HTML untuk tampilan cetak (server-side).
    Mendukung: heading, tebal, kode inline, blok kode, daftar, tabel pipa, garis."""
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def inline(s):
        s = esc(s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    lines = (md or "").split("\n")
    html = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        st = line.strip()
        if st.startswith("```"):
            i += 1
            code = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            html.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
            continue
        if ("|" in line and i + 1 < n
                and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1])
                and "-" in lines[i + 1]):
            header = [c.strip() for c in st.strip("|").split("|")]
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join("<th>" + inline(c) + "</th>" for c in header)
            trs = ""
            for r in rows:
                trs += "<tr>" + "".join("<td>" + inline(c) + "</td>" for c in r) + "</tr>"
            html.append("<table><thead><tr>" + th + "</tr></thead><tbody>" + trs + "</tbody></table>")
            continue
        if not st:
            i += 1
            continue
        if st == "---":
            html.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", st)
        if m:
            lvl = len(m.group(1))
            html.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue
        if re.match(r"^[-*]\s+", st):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>")
                i += 1
            html.append("<ul>" + "".join(items) + "</ul>")
            continue
        html.append("<p>" + inline(st) + "</p>")
        i += 1
    return "\n".join(html)


_PRINT_CSS = (
    "body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111;"
    "max-width:820px;margin:32px auto;padding:0 24px;line-height:1.55;}"
    "h1{font-size:24px;margin:0 0 4px;}h2{font-size:18px;margin:22px 0 8px;}"
    "h3{font-size:15px;margin:16px 0 6px;}p{margin:0 0 10px;}"
    "ul{margin:0 0 12px 22px;}li{margin:2px 0;}"
    "code{background:#f2f2f2;padding:1px 5px;border-radius:4px;"
    "font-family:Consolas,monospace;font-size:.92em;}"
    "pre{background:#f6f8fa;border:1px solid #e2e8f0;border-radius:8px;"
    "padding:12px;overflow:auto;}pre code{background:none;padding:0;}"
    "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;}"
    "th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left;}"
    "th{background:#f1f5f9;}hr{border:none;border-top:1px solid #e2e8f0;margin:18px 0;}"
    ".toolbar{margin:0 0 18px;padding:10px 0;border-bottom:1px solid #e2e8f0;}"
    ".toolbar button{padding:8px 16px;border:1px solid #2563eb;background:#2563eb;"
    "color:#fff;border-radius:8px;cursor:pointer;font-size:14px;}"
    "@media print{.toolbar{display:none;}}"
)


async def api_laporan_export(request: Request):
    q = request.query_params
    rid = q.get("id")
    fmt = (q.get("fmt") or "md").strip().lower()

    def _run():
        conn = reports_db.init_db(reports_db.connect())
        try:
            return reports_db.get_report(conn, rid)
        finally:
            conn.close()
    try:
        rep = await run_in_threadpool(_run)
    except Exception as e:
        return Response("Gagal memuat laporan: %s" % e, media_type="text/plain", status_code=500)
    if not rep:
        return Response("Laporan tidak ditemukan.", media_type="text/plain", status_code=404)

    title = rep.get("title") or "Laporan"
    md = rep.get("content_md") or ""
    fname = _safe_filename(title)

    if fmt == "md":
        headers = {"Content-Disposition": 'attachment; filename="%s.md"' % fname}
        return Response(content=md, media_type="text/markdown; charset=utf-8", headers=headers)

    # PDF: tampilan cetak rapi + auto-print (Simpan sebagai PDF via browser).
    body_html = _md_to_html(md)
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    doc = (
        "<!DOCTYPE html><html lang=\"id\"><head><meta charset=\"utf-8\">"
        "<title>" + safe_title + "</title><style>" + _PRINT_CSS + "</style></head><body>"
        "<div class=\"toolbar\"><button onclick=\"window.print()\">Simpan sebagai PDF / Cetak</button></div>"
        + body_html +
        "<script>window.addEventListener('load',function(){setTimeout(function(){window.print();},350);});</script>"
        "</body></html>"
    )
    return HTMLResponse(content=doc)


async def laporan_page(request: Request):
    return render_page(request, "laporan.html", "laporan")


def register(app):
    app.add_api_route("/laporan", laporan_page, methods=["GET"])
    app.add_api_route("/api/laporan/generate", api_laporan_generate, methods=["POST"])
    app.add_api_route("/api/laporan/save", api_laporan_save, methods=["POST"])
    app.add_api_route("/api/laporan/list", api_laporan_list, methods=["GET"])
    app.add_api_route("/api/laporan/get", api_laporan_get, methods=["GET"])
    app.add_api_route("/api/laporan/delete", api_laporan_delete, methods=["POST"])
    app.add_api_route("/api/laporan/export", api_laporan_export, methods=["GET"])
