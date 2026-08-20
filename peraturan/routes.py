# -*- coding: utf-8 -*-
"""peraturan_routes.py — Menu \"Peraturan\" (sumber resource #5).

Halaman admin untuk mengelola basis data peraturan perpajakan (hasil migrasi
dari repositori jakai). Menyediakan:
  * GET  /peraturan                     -> halaman kelola (admin)
  * POST /api/peraturan/list            -> daftar peraturan (grouped) + filter
  * POST /api/peraturan/get             -> semua unit satu peraturan (tersusun)
  * POST /api/peraturan/save            -> tambah/ubah satu unit
  * POST /api/peraturan/delete          -> hapus per nomor (bulk_delete)
  * POST /api/peraturan/status          -> ubah status berlaku/dicabut/diubah
  * POST /api/peraturan/import-html     -> impor 1 halaman HTML TKB DJP
  * POST /api/peraturan/import-jsonl    -> impor baris JSONL (peraturan_unit)
  * POST /api/peraturan/batch           -> impor massal folder di LATAR (+OCR opsional)
  * GET  /api/peraturan/batch-progress  -> pantau progres batch/OCR berjalan
  * POST /api/peraturan/audit           -> rekonsiliasi berkas folder vs DB (ada/belum)
  * POST /api/peraturan/reindex         -> hitung ulang embedding e5
  * GET  /api/peraturan/stats           -> ringkasan angka
  * GET  /api/peraturan/impor-log       -> log triase impor
  * POST /api/peraturan/search          -> uji retrieval hybrid

Catatan batch: /batch kini MEMULAI proses di thread latar & langsung kembali
({ok, started}); UI mem-poll /batch-progress (fase, berkas i/total, OCR yang
sedang diproses + halaman berjalan, ringkasan akhir) agar OCR bisa dipantau.

Catatan audit: /audit menelusuri folder + subfolder lalu mencocokkan tiap
berkas ke DB TANPA menulis apa pun; berguna untuk melacak berkas yang BELUM
masuk basis data saat folder dianggap sudah lengkap.

Akses dibatasi admin lewat _route_area di app_core (area 'peraturan').
Daftarkan dengan:  import peraturan_routes; peraturan_routes.register(app)
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import peraturan.db as pdb
import peraturan.semantic as psem

try:
    import peraturan.parser as tkb_djp
except Exception:            # pragma: no cover
    tkb_djp = None
try:
    import peraturan.batch as pbatch
except Exception:            # pragma: no cover
    pbatch = None


async def _body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


# ------------------------------------------------------------------ halaman
async def page_peraturan(request: Request):
    extra = {"embed_aktif": False, "model_id": ""}
    try:
        extra["embed_aktif"] = bool(psem.is_available())
        extra["model_id"] = psem.model_id()
    except Exception:
        pass
    return render_page(request, "peraturan.html", "peraturan", extra)


# -------------------------------------------------------------------- daftar
async def api_list(request: Request):
    b = await _body(request)
    try:
        res = await run_in_threadpool(
            pdb.list_peraturan_grouped,
            (b.get("q") or "").strip(),
            (b.get("jenis") or "").strip(),
            (b.get("status") or "").strip(),
            (b.get("lampiran") or "").strip(),
            int(b.get("limit") or 200),
            int(b.get("offset") or 0),
        )
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_get(request: Request):
    b = await _body(request)
    nomor = (b.get("nomor") or "").strip()
    jenis = (b.get("jenis") or "").strip() or None
    if not nomor:
        return JSONResponse({"ok": False, "error": "nomor kosong."})
    try:
        units = await run_in_threadpool(pdb.peraturan_tersusun, nomor, jenis)
        return JSONResponse({"ok": True, "units": units, "total": len(units)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ------------------------------------------------------------------- tulis
def _do_save(data):
    conn = pdb.init_db(pdb.connect())
    try:
        return pdb.upsert_peraturan(data, conn=conn)
    finally:
        conn.close()


async def api_save(request: Request):
    b = await _body(request)
    data = {k: b.get(k) for k in pdb.PERATURAN_KOLOM if k in b}
    if not (data.get("id") or "").strip():
        return JSONResponse({"ok": False, "error": "Field 'id' wajib diisi."})
    try:
        res = await run_in_threadpool(_do_save, data)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_delete(request: Request):
    b = await _body(request)
    keys = b.get("keys")
    id_ = (b.get("id") or "").strip()
    try:
        if id_:
            await run_in_threadpool(pdb.delete_peraturan, id_)
            return JSONResponse({"ok": True, "unit_dihapus": 1})
        if isinstance(keys, list) and keys:
            res = await run_in_threadpool(pdb.bulk_delete, keys)
            return JSONResponse({"ok": True, **res})
        return JSONResponse({"ok": False, "error": "Sediakan 'id' atau 'keys'."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_status(request: Request):
    b = await _body(request)
    status = (b.get("status") or "").strip()
    keys = b.get("keys") if isinstance(b.get("keys"), list) else None
    source_ids = b.get("source_ids") if isinstance(b.get("source_ids"), list) else None
    extra = b.get("extra") if isinstance(b.get("extra"), dict) else None
    if status not in ("berlaku", "dicabut", "diubah"):
        return JSONResponse({"ok": False, "error": "status harus berlaku/dicabut/diubah."})
    try:
        res = await run_in_threadpool(pdb.bulk_update_status, status, keys, source_ids, extra)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ------------------------------------------------------------------- impor
def _import_html(html, per_ayat, jenis_hint):
    if tkb_djp is None:
        return {"ok": False, "error": "Parser tidak tersedia (butuh beautifulsoup4 + lxml)."}
    meta, rows = tkb_djp.to_rows(html, per_ayat=per_ayat, jenis_hint=jenis_hint or None)
    if not rows:
        return {"ok": False, "error": "Tidak ada unit terparse dari HTML."}
    conn = pdb.init_db(pdb.connect())
    try:
        for r in rows:
            r["source_id"] = meta.base_id
            pdb.upsert_peraturan(r, conn=conn)
    finally:
        conn.close()
    return {"ok": True, "nomor": meta.nomor, "jenis": meta.jenis_peraturan,
            "n_unit": len(rows), "source_id": meta.base_id}


async def api_import_html(request: Request):
    b = await _body(request)
    html = b.get("html") or ""
    if not html.strip():
        return JSONResponse({"ok": False, "error": "Field 'html' kosong."})
    try:
        res = await run_in_threadpool(
            _import_html, html, bool(b.get("per_ayat")), (b.get("jenis_hint") or "").strip())
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def _import_jsonl(text):
    conn = pdb.init_db(pdb.connect())
    n = gagal = 0
    errors = []
    try:
        for i, line in enumerate((text or "").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("id"):
                    raise ValueError("baris tanpa 'id'")
                pdb.upsert_peraturan(row, conn=conn)
                n += 1
            except Exception as e:
                gagal += 1
                if len(errors) < 10:
                    errors.append("baris %d: %s" % (i, str(e)[:120]))
    finally:
        conn.close()
    return {"ok": True, "masuk": n, "gagal": gagal, "errors": errors}


async def api_import_jsonl(request: Request):
    b = await _body(request)
    text = b.get("jsonl") or b.get("text") or ""
    if not text.strip():
        return JSONResponse({"ok": False, "error": "Field 'jsonl' kosong."})
    try:
        res = await run_in_threadpool(_import_jsonl, text)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_batch(request: Request):
    """Mulai batch folder di thread latar & kembali segera.

    Kembalikan {ok, started} bila berhasil dimulai, atau {ok:false, running}
    bila sudah ada batch berjalan. Pantau kemajuan lewat /batch-progress.
    """
    b = await _body(request)
    root = (b.get("root") or "").strip()
    if not root:
        return JSONResponse({"ok": False, "error": "Field 'root' (folder) kosong."})
    if pbatch is None:
        return JSONResponse({"ok": False, "error": "Modul batch tidak tersedia."})
    try:
        res = await run_in_threadpool(
            pbatch.proses_async, root, bool(b.get("per_ayat")),
            bool(b.get("do_ocr")), bool(b.get("ingest", True)))
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_batch_progress(request: Request):
    """Snapshot progres batch berjalan/terakhir (untuk polling UI)."""
    if pbatch is None:
        return JSONResponse({"ok": False, "error": "Modul batch tidak tersedia."})
    try:
        prog = await run_in_threadpool(pbatch.get_progress)
        return JSONResponse({"ok": True, "progress": prog})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_audit(request: Request):
    """Rekonsiliasi folder <-> DB: berkas mana yang sudah/belum masuk basis data.

    Body: {root, status?('' | 'belum' | 'induk_ada' | 'ada' | 'abaikan'), limit?}.
    Tidak menulis apa pun ke DB. Kembalikan ringkasan + daftar berkas.
    """
    b = await _body(request)
    root = (b.get("root") or "").strip()
    if not root:
        return JSONResponse({"ok": False, "error": "Field 'root' (folder) kosong."})
    if pbatch is None:
        return JSONResponse({"ok": False, "error": "Modul batch tidak tersedia."})
    status_filter = (b.get("status") or "").strip()
    try:
        limit = int(b.get("limit") or 5000)
    except Exception:
        limit = 5000
    try:
        res = await run_in_threadpool(pbatch.audit_folder, root, status_filter, limit)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_reindex(request: Request):
    try:
        res = await run_in_threadpool(pdb.reindex)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_stats(request: Request):
    try:
        res = await run_in_threadpool(pdb.stats)
        res["embed_aktif"] = bool(psem.is_available())
        res["model_id"] = psem.model_id()
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_impor_log(request: Request):
    status = request.query_params.get("status", "")
    try:
        rows = await run_in_threadpool(pdb.list_impor_log, status, 800)
        return JSONResponse({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_search(request: Request):
    b = await _body(request)
    q = (b.get("q") or b.get("query") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "query kosong."})
    k = int(b.get("k") or 8)
    st = b.get("status_list")
    status_list = tuple(st) if isinstance(st, list) and st else ("berlaku",)
    try:
        rows = await run_in_threadpool(pdb.search, q, k, status_list)
        return JSONResponse({"ok": True, "hasil": rows, "total": len(rows),
                             "embed_aktif": bool(psem.is_available())})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/peraturan", page_peraturan, methods=["GET"])
    app.add_api_route("/api/peraturan/list", api_list, methods=["POST"])
    app.add_api_route("/api/peraturan/get", api_get, methods=["POST"])
    app.add_api_route("/api/peraturan/save", api_save, methods=["POST"])
    app.add_api_route("/api/peraturan/delete", api_delete, methods=["POST"])
    app.add_api_route("/api/peraturan/status", api_status, methods=["POST"])
    app.add_api_route("/api/peraturan/import-html", api_import_html, methods=["POST"])
    app.add_api_route("/api/peraturan/import-jsonl", api_import_jsonl, methods=["POST"])
    app.add_api_route("/api/peraturan/batch", api_batch, methods=["POST"])
    app.add_api_route("/api/peraturan/batch-progress", api_batch_progress, methods=["GET"])
    app.add_api_route("/api/peraturan/audit", api_audit, methods=["POST"])
    app.add_api_route("/api/peraturan/reindex", api_reindex, methods=["POST"])
    app.add_api_route("/api/peraturan/stats", api_stats, methods=["GET"])
    app.add_api_route("/api/peraturan/impor-log", api_impor_log, methods=["GET"])
    app.add_api_route("/api/peraturan/search", api_search, methods=["POST"])
