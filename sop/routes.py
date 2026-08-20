# -*- coding: utf-8 -*-
"""sop_routes.py — Halaman & API menu SOP / Proses Bisnis.

Endpoint:
  GET  /sop                    -> halaman UI (templates/sop.html)
  POST /api/sop/list           -> daftar dokumen (grouped) + filter/paging
  POST /api/sop/get            -> semua bagian satu dokumen
  POST /api/sop/save           -> upsert satu unit (edit manual)
  POST /api/sop/delete         -> hapus dokumen (per dokumen_id)
  POST /api/sop/batch          -> mulai impor folder (async)
  GET  /api/sop/batch-progress -> pantau progres impor + OCR
  POST /api/sop/audit          -> rekonsiliasi folder vs database
  POST /api/sop/reindex        -> hitung ulang embedding e5
  POST /api/sop/search         -> uji retrieval hybrid
  GET  /api/sop/stats          -> ringkasan basis data
  GET  /api/sop/impor-log      -> log impor terakhir (opsi filter status)

Grup menu memakai area 'peraturan' (lihat app_core._route_area) sehingga hak
akses admin yang sama berlaku; tak perlu peran baru.

Catatan: setiap handler WAJIB menganotasi parameter sebagai `request: Request`.
Tanpa anotasi itu, FastAPI (add_api_route) menganggap `request` sebagai query
parameter wajib sehingga memunculkan error {\"loc\":[\"query\",\"request\"]}.
"""
import json
import os

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page
import sop_db as sdb
import sop_batch as sbatch

try:
    import peraturan_semantic as psem
except Exception:
    psem = None


async def _body(request: Request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))
    except Exception:
        try:
            form = await request.form()
            return dict(form)
        except Exception:
            return {}


# ------------------------------------------------------------------- halaman
async def page_sop(request: Request):
    extra = {
        "embed_aktif": bool(psem.is_available()) if psem else False,
        "model_id": (psem.model_id() if psem else "") or "",
    }
    return render_page(request, "sop.html", "sop", extra)


# ----------------------------------------------------------------------- API
async def api_list(request: Request):
    try:
        d = await _body(request)
        q = (d.get("q") or "").strip()
        kategori = (d.get("kategori") or "").strip()
        limit = int(d.get("limit") or 200)
        offset = int(d.get("offset") or 0)
        res = await run_in_threadpool(sdb.list_dokumen_grouped, q, kategori, limit, offset)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_get(request: Request):
    try:
        d = await _body(request)
        did = (d.get("dokumen_id") or "").strip()
        if not did:
            return JSONResponse({"ok": False, "error": "dokumen_id wajib"}, status_code=400)
        rows = await run_in_threadpool(sdb.get_dokumen, did)
        return JSONResponse({"ok": True, "units": rows})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_save(request: Request):
    try:
        d = await _body(request)
        if not d.get("id"):
            return JSONResponse({"ok": False, "error": "id wajib"}, status_code=400)
        res = await run_in_threadpool(sdb.upsert_sop, d)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_delete(request: Request):
    try:
        d = await _body(request)
        did = (d.get("dokumen_id") or "").strip()
        dids = d.get("dokumen_ids")
        if dids:
            res = await run_in_threadpool(sdb.bulk_delete_dokumen, dids)
        elif did:
            res = await run_in_threadpool(sdb.delete_dokumen, did)
        else:
            return JSONResponse({"ok": False, "error": "dokumen_id wajib"}, status_code=400)
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_batch(request: Request):
    try:
        d = await _body(request)
        root = (d.get("root") or "").strip()
        if not root:
            return JSONResponse({"ok": False, "error": "root (folder) wajib"}, status_code=400)
        do_ocr = bool(d.get("do_ocr") or d.get("ocr"))
        ingest = d.get("ingest", True)
        ingest = bool(ingest) if ingest is not None else True
        use_ai = bool(d.get("ai_naming", d.get("use_ai_naming", True)))
        do_ringkas = d.get("ringkas", True)
        do_ringkas = bool(do_ringkas) if do_ringkas is not None else True
        res = sbatch.proses_async(root, do_ocr=do_ocr, ingest=ingest,
                                  use_ai_naming=use_ai, do_ringkas=do_ringkas)
        code = 200 if res.get("ok") else 409
        return JSONResponse(res, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _flag(form, name, default=False):
    v = form.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "on", "ya", "yes")


async def api_upload(request: Request):
    """Terima unggahan berkas SOP (multipart), simpan, lalu proses async."""
    try:
        form = await request.form()
        ups = form.getlist("files")
        if not ups:
            one = form.get("file")
            if one is not None:
                ups = [one]
        do_ocr = _flag(form, "do_ocr") or _flag(form, "ocr")
        use_ai = _flag(form, "ai_naming", True)
        do_ringkas = _flag(form, "ringkas", True)
        updir = sbatch.upload_dir()
        saved = []
        for uf in ups:
            nama = os.path.basename((getattr(uf, "filename", "") or "").strip())
            if not nama:
                continue
            data = await uf.read()
            if isinstance(data, str):
                data = data.encode("utf-8", "replace")
            dest = os.path.join(updir, nama)
            with open(dest, "wb") as w:
                w.write(data)
            saved.append(dest)
        if not saved:
            return JSONResponse({"ok": False, "error": "tak ada berkas valid diunggah"},
                                status_code=400)
        res = sbatch.proses_files_async(saved, root=updir, do_ocr=do_ocr,
                                        use_ai_naming=use_ai, do_ringkas=do_ringkas)
        res["disimpan"] = len(saved)
        code = 200 if res.get("ok") else 409
        return JSONResponse(res, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_batch_progress(request: Request):
    try:
        return JSONResponse({"ok": True, "progress": sbatch.get_progress()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_audit(request: Request):
    try:
        d = await _body(request)
        root = (d.get("root") or "").strip()
        if not root:
            return JSONResponse({"ok": False, "error": "root (folder) wajib"}, status_code=400)
        status = (d.get("status") or "").strip()
        limit = int(d.get("limit") or 5000)
        res = await run_in_threadpool(sbatch.audit_folder, root, status, limit)
        code = 200 if res.get("ok") else 400
        return JSONResponse(res, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_reindex(request: Request):
    try:
        res = await run_in_threadpool(sdb.reindex)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_search(request: Request):
    try:
        d = await _body(request)
        q = (d.get("q") or d.get("query") or "").strip()
        k = int(d.get("k") or 8)
        if not q:
            return JSONResponse({"ok": False, "error": "q wajib"}, status_code=400)
        rows = await run_in_threadpool(sdb.search, q, k)
        return JSONResponse({"ok": True, "hits": rows})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_stats(request: Request):
    try:
        res = await run_in_threadpool(sdb.stats)
        return JSONResponse({"ok": True, "stats": res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_impor_log(request: Request):
    try:
        status = request.query_params.get("status", "")
        limit = int(request.query_params.get("limit", "800"))
        rows = await run_in_threadpool(sdb.list_impor_log, status, limit)
        return JSONResponse({"ok": True, "rows": rows})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def register(app):
    app.add_api_route("/sop", page_sop, methods=["GET"])
    app.add_api_route("/api/sop/list", api_list, methods=["POST"])
    app.add_api_route("/api/sop/get", api_get, methods=["POST"])
    app.add_api_route("/api/sop/save", api_save, methods=["POST"])
    app.add_api_route("/api/sop/delete", api_delete, methods=["POST"])
    app.add_api_route("/api/sop/batch", api_batch, methods=["POST"])
    app.add_api_route("/api/sop/upload", api_upload, methods=["POST"])
    app.add_api_route("/api/sop/batch-progress", api_batch_progress, methods=["GET"])
    app.add_api_route("/api/sop/audit", api_audit, methods=["POST"])
    app.add_api_route("/api/sop/reindex", api_reindex, methods=["POST"])
    app.add_api_route("/api/sop/search", api_search, methods=["POST"])
    app.add_api_route("/api/sop/stats", api_stats, methods=["GET"])
    app.add_api_route("/api/sop/impor-log", api_impor_log, methods=["GET"])
