# -*- coding: utf-8 -*-
"""data_routes.py — Menu Kelola Data: ingest (tarik/impor) & status kelengkapan.
Migrasi langkah 4 dari web_app.py.

Daftarkan dengan:
    import data_routes; data_routes.register(app)
"""
import io
import re
import json
import zipfile

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool

import db.analytics_db as adb
import ingest
from app_core import render_page


async def api_ingest(request: Request):
    """Tarik data ke database (dipakai halaman Kelola Data). Ingest PINTAR:
    hanya menarik hari yang belum ada / belum lengkap, kecuali force=true."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    lang = (body.get("lang") or "id").strip().lower()
    if lang not in ("id", "en"):
        lang = "id"
    force = bool(body.get("force"))
    preset = (body.get("range") or "").strip().lower()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    if preset in ("now", "today"):
        s, e = adb.resolve_range("today")
    elif preset == "yesterday":
        s, e = adb.resolve_range("yesterday")
    elif preset in ("7d", "30d", "90d"):
        s, e = adb.resolve_range(preset)
    elif preset == "all":
        return JSONResponse({"ok": False,
                             "error": "Pilih rentang tanggal spesifik untuk menarik data."})
    else:
        s, e = (start or None), (end or start or None)
    if not s or not e:
        return JSONResponse({"ok": False, "error": "Rentang tanggal tidak valid."})
    try:
        res = await run_in_threadpool(ingest.ensure_range, s, e, lang, None, force, False)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def _parse_log_bytes(data):
    # bytes -> list entri log (JSON array / objek / JSON Lines / pembungkus
    # {"entries":[...]} atau {"data":[...]}). Sama dengan logika Step 2.
    decoded = data.decode("utf-8", "replace")
    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]
    decoded = decoded.strip()
    if not decoded:
        return []
    items = []
    parsed = None
    try:
        parsed = json.loads(decoded)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        if isinstance(parsed.get("entries"), list):
            items = parsed["entries"]
        elif isinstance(parsed.get("data"), list):
            items = parsed["data"]
        else:
            items = [parsed]
    else:
        for line in re.split(r"\r?\n", decoded):
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except Exception:
                continue
            if isinstance(v, list):
                items.extend(v)
            elif isinstance(v, dict):
                items.append(v)
    return [it for it in items if isinstance(it, dict)]


def _parse_log_upload(data, name):
    # bytes (satu file) -> list entri. Mendukung .zip berisi banyak file JSON.
    low = (name or "").lower()
    is_zip = low.endswith(".zip") or (len(data) >= 2 and data[:2] == b"PK")
    if is_zip:
        out = []
        zf = zipfile.ZipFile(io.BytesIO(data))
        for info in zf.infolist():
            if info.is_dir():
                continue
            nm = info.filename.lower()
            if not (nm.endswith(".json") or nm.endswith(".jsonl")
                    or nm.endswith(".ndjson") or nm.endswith(".txt")):
                continue
            out.extend(_parse_log_bytes(zf.read(info)))
        return out
    return _parse_log_bytes(data)


async def api_ingest_upload(request: Request):
    # Impor manual: unggah file JSON hasil ekspor Google Cloud Logging langsung
    # ke analytics.db (untuk uji end-to-end / data lampau tanpa akses Google).
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"ok": False, "error": "Form tidak valid."})
    ups = [u for u in form.getlist("file") if isinstance(u, StarletteUploadFile)]
    if not ups:
        return JSONResponse({"ok": False, "error": "Tidak ada file diunggah."})
    lang = (form.get("lang") or "").strip().lower()
    if lang not in ("id", "en"):
        lang = None  # auto: baca lang dari tiap payload
    entries = []
    errors = []
    for u in ups:
        try:
            data = await u.read()
            entries.extend(_parse_log_upload(data, u.filename or "log.json"))
        except Exception as e:
            errors.append("%s: %s" % ((u.filename or "file"), str(e)[:200]))
    if not entries:
        msg = "Tidak ada entri log yang bisa dibaca."
        if errors:
            msg += " " + " | ".join(errors)
        return JSONResponse({"ok": False, "error": msg})
    try:
        res = await run_in_threadpool(ingest.ingest_entries, entries, lang, None, False)
        if errors:
            res["warnings"] = errors
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})





async def data_page(request: Request):
    return render_page(request, "data.html", "data")


async def api_data_status(request: Request):
    """Status kelengkapan data per hari untuk rentang (halaman Kelola Data)."""
    q = request.query_params
    lang = (q.get("lang") or "id").strip().lower()
    if lang not in ("id", "en"):
        lang = "id"
    preset = (q.get("range") or "").strip().lower()
    start = q.get("start") or None
    end = q.get("end") or None
    if preset and preset not in ("", "custom"):
        start, end = adb.resolve_range(preset)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            rs = adb.range_status(conn, start, end, lang) if (start and end) else None
            return {"ok": True, "lang": lang, "status": rs,
                    "bounds": adb.data_bounds(conn),
                    "last_ingest": adb.get_meta(conn, "last_ingest_at"),
                    "today": adb._jkt_today()}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

def register(app):
    app.add_api_route("/api/ingest", api_ingest, methods=["POST"])
    app.add_api_route("/api/ingest-upload", api_ingest_upload, methods=["POST"])
    app.add_api_route("/data", data_page, methods=["GET"])
    app.add_api_route("/api/data/status", api_data_status, methods=["GET"])
