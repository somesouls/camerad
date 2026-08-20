# -*- coding: utf-8 -*-
"""sosmed_routes.py — Rute Menu Sosmed (X / IG / TikTok), versi ringkas 4 menu.

Struktur menu (rombak Agustus 2026, sesuai arahan "cukup jadi database FAQ"):
  1. Q&A                   — gabungan Inbox + Daftar Q&A (pertanyaan warga + utas).
  2. Kelola Data Sosmed    — impor manual / tarik X + housekeeping + perbaiki data.
  3. SLA & Analitik        — gabungan Coverage & SLA + Analitik Sosmed.
  4. Coverage & Deflection — database FAQ: klaster pertanyaan yang lagi trending +
                             cek apakah bot SUDAH punya intent yang menjawab
                             (bila belum -> gap pengetahuan), lengkap draf jawaban.

Semua menu punya filter platform (X / IG / TikTok), siap untuk impor IG & TikTok.

Lapisan data: sosmed_db.py (pairing Q&A sadar-thread). Otak FAQ/gap:
sosmed_knowledge.py. Collector X: sosmed_x.py. Daftarkan dengan:
    import sosmed_routes; sosmed_routes.register(app)
"""
import io
import csv
import json
import zipfile

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool

import sosmed.db as sdb
import sosmed.x as sx
import sosmed.knowledge as sk
from app_core import render_page


def _conn():
    c = sdb.connect()
    sdb.init_db(c)
    return c


def _off_handles():
    return sdb.official_handles()


# ---------------------------------------------------------------------------
# Parsing item dari file/teks (JSON array, JSONL, CSV, payload X API v2, .zip)
# ---------------------------------------------------------------------------
def _looks_like_x_payload(obj):
    return isinstance(obj, dict) and "data" in obj and (
        "includes" in obj or "meta" in obj
        or (isinstance(obj.get("data"), list)
            and obj["data"] and isinstance(obj["data"][0], dict)
            and ("text" in obj["data"][0] or "author_id" in obj["data"][0])))


def _parse_items_text(text, name="", default_platform=None):
    """Parse satu blob teks jadi list item mentah (dict)."""
    name = (name or "").lower()
    text = (text or "").strip()
    if not text:
        return []
    # CSV
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
        return [dict(r) for r in rows]
    # JSON / JSONL
    try:
        obj = json.loads(text)
    except Exception:
        # coba JSONL
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
        return items
    # payload X API v2 -> map lewat collector
    if _looks_like_x_payload(obj):
        return sx.map_tweets_v2(obj, official_handles=_off_handles())
    if isinstance(obj, dict) and isinstance(obj.get("items"), list):
        return obj["items"]
    if isinstance(obj, dict) and isinstance(obj.get("data"), list):
        return obj["data"]
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    return []


def _parse_items_upload(data, name, default_platform=None):
    name = (name or "").lower()
    if name.endswith(".zip"):
        items = []
        zf = zipfile.ZipFile(io.BytesIO(data))
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                raw = zf.read(info).decode("utf-8", "replace")
            except Exception:
                continue
            items.extend(_parse_items_text(raw, info.filename, default_platform))
        return items
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    return _parse_items_text(text, name, default_platform)


# ---------------------------------------------------------------------------
# Halaman menu (4 menu)
# ---------------------------------------------------------------------------
async def sosmed_qna_page(request: Request):
    return render_page(request, "sosmed_qna.html", "sosmed_qna")


async def sosmed_kelola_page(request: Request):
    return render_page(request, "sosmed_kelola.html", "sosmed_kelola")


async def sosmed_sla_page(request: Request):
    return render_page(request, "sosmed_sla.html", "sosmed_sla")


async def sosmed_deflection_page(request: Request):
    return render_page(request, "sosmed_deflection.html", "sosmed_deflection")


# Redirect rute lama -> baru (kompatibilitas bookmark setelah rombak menu)
async def _redir_qna(request: Request):
    return RedirectResponse("/sosmed", status_code=307)


async def _redir_sla(request: Request):
    return RedirectResponse("/sosmed/sla", status_code=307)


async def _redir_deflection(request: Request):
    return RedirectResponse("/sosmed/deflection", status_code=307)


# ---------------------------------------------------------------------------
# Impor manual
# ---------------------------------------------------------------------------
def _current_username(request: Request):
    try:
        u = getattr(request.state, "user", None)
        return (u or {}).get("username") or ""
    except Exception:
        return ""


async def api_import_upload(request: Request):
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"ok": False, "error": "Form tidak valid."}, status_code=400)
    platform = (form.get("platform") or "").strip().lower() or None
    ups = [u for u in form.getlist("file") if isinstance(u, StarletteUploadFile)]
    if not ups:
        return JSONResponse({"ok": False, "error": "Tidak ada berkas diunggah."}, status_code=400)
    items = []
    for u in ups:
        data = await u.read()
        items.extend(_parse_items_upload(data, u.filename or "data.json", platform))
    if not items:
        return JSONResponse({"ok": False, "error": "Tidak ada item terbaca dari berkas."}, status_code=400)
    user = _current_username(request)

    def _do():
        c = _conn()
        try:
            return sdb.ingest_items(c, items, default_platform=platform,
                                    source="import_upload", pulled_by=user)
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    return JSONResponse(res)


async def api_import_paste(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = (body or {}).get("content") or ""
    platform = ((body or {}).get("platform") or "").strip().lower() or None
    fmt = ((body or {}).get("format") or "").strip().lower()
    name = ".csv" if fmt == "csv" else ".json"
    items = _parse_items_text(content, name, platform)
    if not items:
        return JSONResponse({"ok": False, "error": "Tidak ada item terbaca dari teks."}, status_code=400)
    user = _current_username(request)

    def _do():
        c = _conn()
        try:
            return sdb.ingest_items(c, items, default_platform=platform,
                                    source="import_paste", pulled_by=user)
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    return JSONResponse(res)


# ---------------------------------------------------------------------------
# Tarik X (capability-aware)
# ---------------------------------------------------------------------------
async def api_x_capabilities(request: Request):
    cap = await run_in_threadpool(sx.capabilities)
    return JSONResponse({"ok": True, "capabilities": cap})


async def api_pull_x(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    source = ((body or {}).get("source") or "mentions").strip().lower()
    query = ((body or {}).get("query") or "").strip()
    try:
        max_results = int((body or {}).get("max_results") or 50)
    except Exception:
        max_results = 50
    user = _current_username(request)
    off = _off_handles()

    def _do():
        if source == "search":
            q = query or "kringpajak"
            items, info = sx.search_recent(q, max_results=max_results, official_handles=off)
        else:
            items, info = sx.pull_mentions(max_results=max_results, official_handles=off)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error", "Gagal menarik data X."),
                    "capability": True}
        c = _conn()
        try:
            res = sdb.ingest_items(c, items, default_platform="x",
                                   source="pull_x_" + source, pulled_by=user)
        finally:
            c.close()
        res["pulled"] = info.get("count", len(items))
        return res
    res = await run_in_threadpool(_do)
    code = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=code)


async def api_repair(request: Request):
    """Perbaiki data lama: jalankan ulang pairing Q&A sadar-thread untuk SEMUA
    conversation. Dipakai sekali setelah upgrade agar baris lama (yang di-ingest
    versi backend lama) mendapat item_type/status yang benar."""
    def _do():
        c = _conn()
        try:
            return sdb.repair_all_pairing(c)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# Q&A (dulu Inbox / Daftar Q&A)
# ---------------------------------------------------------------------------
def _qp(request, key, default=""):
    return (request.query_params.get(key) or default).strip()


async def api_list(request: Request):
    q = request.query_params
    try:
        limit = int(q.get("limit") or 200)
    except Exception:
        limit = 200
    only_q = (q.get("only_questions") or "").lower() in ("1", "true", "ya", "yes")

    def _do():
        c = _conn()
        try:
            return sdb.list_items(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                topik=_qp(request, "topik"), status=_qp(request, "status"),
                sentiment=_qp(request, "sentiment"),
                item_type=_qp(request, "item_type"),
                handle=_qp(request, "handle"), q=_qp(request, "q"),
                only_questions=only_q, limit=limit)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_thread(request: Request):
    platform = _qp(request, "platform", "x")
    conv = _qp(request, "conversation_id") or _qp(request, "conv")
    if not conv:
        return JSONResponse({"ok": False, "error": "conversation_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            return sdb.get_thread(c, platform, conv)
        finally:
            c.close()
    r = await run_in_threadpool(_do)
    if not r:
        return JSONResponse({"ok": False, "error": "Thread tidak ditemukan."}, status_code=404)
    return JSONResponse(r)


async def api_set_status(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    item_id = (body or {}).get("id")
    status = ((body or {}).get("status") or "").strip()
    if not item_id or status not in sdb.STATUSES:
        return JSONResponse({"ok": False, "error": "id/status tidak valid."}, status_code=400)

    def _do():
        c = _conn()
        try:
            ok = sdb.set_status(c, int(item_id), status)
            return {"ok": ok}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_set_topik(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    item_id = (body or {}).get("id")
    topik = ((body or {}).get("topik") or "").strip()
    if not item_id:
        return JSONResponse({"ok": False, "error": "id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            ok = sdb.set_topik(c, int(item_id), topik or None)
            return {"ok": ok}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# SLA & Analitik (gabungan Coverage & SLA + Analitik)
# ---------------------------------------------------------------------------
async def api_coverage(request: Request):
    def _do():
        c = _conn()
        try:
            return sdb.coverage_sla(c, platform=_qp(request, "platform"),
                                    range_=_qp(request, "range", "all"),
                                    start=_qp(request, "start"), end=_qp(request, "end"))
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_analytics(request: Request):
    def _do():
        c = _conn()
        try:
            return sdb.analytics(c, platform=_qp(request, "platform"),
                                 range_=_qp(request, "range", "all"),
                                 start=_qp(request, "start"), end=_qp(request, "end"))
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# Coverage & Deflection (database FAQ + gap pengetahuan bot)
# ---------------------------------------------------------------------------
async def api_knowledge_gap(request: Request):
    q = request.query_params
    try:
        min_count = int(q.get("min_count") or 1)
    except Exception:
        min_count = 1
    use_sem = (q.get("semantic") or "1").lower() not in ("0", "false", "no", "")

    def _do():
        c = _conn()
        try:
            return sk.knowledge_gap(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                min_count=min_count, use_semantic=use_sem)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# Stats / housekeeping
# ---------------------------------------------------------------------------
async def api_stats(request: Request):
    def _do():
        c = _conn()
        try:
            return {"ok": True, "stats": sdb.stats(c), "batches": sdb.list_batches(c, 50)}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_purge(request: Request):
    def _do():
        c = _conn()
        try:
            sdb.purge_all(c)
            return {"ok": True}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# Registrasi
# ---------------------------------------------------------------------------
def register(app):
    # Halaman (4 menu)
    app.add_api_route("/sosmed", sosmed_qna_page, methods=["GET"])
    app.add_api_route("/sosmed/qna", sosmed_qna_page, methods=["GET"])
    app.add_api_route("/sosmed/kelola", sosmed_kelola_page, methods=["GET"])
    app.add_api_route("/sosmed/sla", sosmed_sla_page, methods=["GET"])
    app.add_api_route("/sosmed/deflection", sosmed_deflection_page, methods=["GET"])
    # Redirect rute lama -> baru
    app.add_api_route("/sosmed/inbox", _redir_qna, methods=["GET"])
    app.add_api_route("/sosmed/coverage", _redir_sla, methods=["GET"])
    app.add_api_route("/sosmed/analytics", _redir_sla, methods=["GET"])
    app.add_api_route("/sosmed/faq", _redir_deflection, methods=["GET"])
    # Kelola data
    app.add_api_route("/api/sosmed/import-upload", api_import_upload, methods=["POST"])
    app.add_api_route("/api/sosmed/import-paste", api_import_paste, methods=["POST"])
    app.add_api_route("/api/sosmed/x/capabilities", api_x_capabilities, methods=["GET"])
    app.add_api_route("/api/sosmed/pull-x", api_pull_x, methods=["POST"])
    app.add_api_route("/api/sosmed/purge", api_purge, methods=["POST"])
    app.add_api_route("/api/sosmed/repair", api_repair, methods=["POST"])
    # Q&A
    app.add_api_route("/api/sosmed/list", api_list, methods=["GET"])
    app.add_api_route("/api/sosmed/thread", api_thread, methods=["GET"])
    app.add_api_route("/api/sosmed/status", api_set_status, methods=["POST"])
    app.add_api_route("/api/sosmed/topik", api_set_topik, methods=["POST"])
    # SLA & Analitik
    app.add_api_route("/api/sosmed/coverage", api_coverage, methods=["GET"])
    app.add_api_route("/api/sosmed/analytics", api_analytics, methods=["GET"])
    # Coverage & Deflection (FAQ + gap)
    app.add_api_route("/api/sosmed/knowledge-gap", api_knowledge_gap, methods=["GET"])
    # kompat: endpoint FAQ lama -> knowledge-gap
    app.add_api_route("/api/sosmed/faq", api_knowledge_gap, methods=["GET"])
    app.add_api_route("/api/sosmed/stats", api_stats, methods=["GET"])
