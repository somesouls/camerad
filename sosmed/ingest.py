# -*- coding: utf-8 -*-
"""
sosmed_ingest.py — Endpoint ingest khusus untuk Camerad X-Scraper (ekstensi).

Tujuan: menerima kiriman NDJSON/JSON dari ekstensi browser (tombol "Kirim ke
 Server") lalu memasukkannya ke database Sosmed lewat sosmed_db.ingest_items.

Dibuat sebagai MODUL TERPISAH agar tidak perlu mengubah file besar
(sosmed_db.py / sosmed_routes.py). Cukup daftarkan di web_app.py:

    import sosmed.ingest as sosmed_ingest
    sosmed_ingest.register(app)

Endpoint:
    POST    /api/sosmed/ingest     -> terima NDJSON (Content-Type
                                       application/x-ndjson / text/plain),
                                       atau JSON array, atau {"items": [...]},
                                       atau multipart file field "file".
    OPTIONS /api/sosmed/ingest     -> preflight CORS (permisif, utk ekstensi).

Catatan: created_at dari X berformat "Tue Aug 04 15:14:42 +0000 2026".
Ekstensi sudah menormalkan ke ISO-8601, tapi di sini kita normalkan ulang
(belt-and-suspenders) supaya file NDJSON lama pun tetap terbaca.
"""
import json
import datetime as _dt

from fastapi import Request
from fastapi.responses import JSONResponse, Response

import sosmed.db as sdb

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

_TW_FMT = "%a %b %d %H:%M:%S %z %Y"  # format created_at legacy Twitter


def _to_iso(val):
    """Normalkan created_at ke ISO-8601. Menerima format Twitter legacy,
    ISO, atau epoch. Kembalikan string ISO atau nilai asli bila gagal."""
    if val is None or val == "":
        return val
    if isinstance(val, (int, float)):
        try:
            return _dt.datetime.utcfromtimestamp(float(val)).isoformat() + "Z"
        except Exception:
            return val
    s = str(val).strip()
    # Sudah ISO? biarkan.
    try:
        _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return s
    except Exception:
        pass
    # Format Twitter legacy
    try:
        d = _dt.datetime.strptime(s, _TW_FMT)
        return d.astimezone(_dt.timezone.utc).isoformat()
    except Exception:
        return s


def _parse_ndjson_or_json(text):
    """Kembalikan list[dict] dari NDJSON, JSON array, atau {items:[...]}. """
    text = (text or "").strip()
    if not text:
        return []
    # Coba JSON utuh dulu (array / objek)
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            if isinstance(obj.get("items"), list):
                return [x for x in obj["items"] if isinstance(x, dict)]
            return [obj]
    except Exception:
        pass
    # NDJSON: satu objek JSON per baris
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if isinstance(o, dict):
                items.append(o)
        except Exception:
            continue
    return items


def _normalize(items):
    for it in items:
        if not isinstance(it, dict):
            continue
        if "created_at" in it:
            it["created_at"] = _to_iso(it.get("created_at"))
        # media list -> simpan sebagai JSON string kalau perlu (sosmed_db
        # menyimpan raw_json; media list biarkan, _norm_item mengabaikannya
        # bila tak dikenal).
    return items


def register(app):
    @app.options("/api/sosmed/ingest")
    async def sosmed_ingest_options():
        return Response(status_code=204, headers=_CORS)

    @app.post("/api/sosmed/ingest")
    async def sosmed_ingest(request: Request):
        items = []
        ctype = (request.headers.get("content-type") or "").lower()
        try:
            if "multipart/form-data" in ctype:
                form = await request.form()
                f = form.get("file")
                if f is not None and hasattr(f, "read"):
                    raw = await f.read()
                    text = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                    items = _parse_ndjson_or_json(text)
                else:
                    text = form.get("text") or form.get("data") or ""
                    items = _parse_ndjson_or_json(str(text))
            else:
                raw = await request.body()
                text = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                items = _parse_ndjson_or_json(text)
        except Exception as e:
            return JSONResponse({"ok": False, "error": "gagal baca body: %s" % e}, status_code=400, headers=_CORS)

        if not items:
            return JSONResponse({"ok": False, "error": "tidak ada data valid (NDJSON/JSON kosong)"}, status_code=400, headers=_CORS)

        items = _normalize(items)

        try:
            conn = sdb.connect()
            sdb.init_db(conn)
            res = sdb.ingest_items(conn, items, default_platform="x", source="extension", pulled_by="extension")
            try:
                conn.close()
            except Exception:
                pass
        except Exception as e:
            return JSONResponse({"ok": False, "error": "ingest gagal: %s" % e}, status_code=500, headers=_CORS)

        out = {"ok": True}
        if isinstance(res, dict):
            out.update(res)
        return JSONResponse(out, headers=_CORS)
