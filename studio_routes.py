# -*- coding: utf-8 -*-
"""Studio Dokumen (Epik C) — route FastAPI.

Dipasang dari web_app.py via register(app, ...). Semua keluaran = dokumen untuk
diunduh / preview (bukan tulis balik ke DB). Isi jawaban HANYA dari dokumen
terunggah + DB global (konsisten Epik B); LLM cloud hanya merangkai kalimat.
"""
import os
import re
import json
import datetime as _dt

from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool
from fastapi import Request
from fastapi.responses import JSONResponse, Response, PlainTextResponse

import docstudio as dstudio
import pii_mask

MAX_STUDIO_BYTES = 25 * 1024 * 1024   # 25 MB
MAP_CHUNK_LIMIT = 14                  # maksimum potongan yang diproses tahap map


def register(app, *, base_dir, render_page, llm_client, kctx, xlsx_mime):
    STUDIO_DIR = os.path.join(base_dir, "_studio")

    # ---- Submenu Analitik AWE (Avaya): dipasang di sini agar TIDAK perlu
    #      menyentuh web_app.py (file besar). Menyediakan 5 halaman + API. ----
    try:
        import awe_analytics
        awe_analytics.register(app, render_page=render_page)
    except Exception:
        import traceback
        traceback.print_exc()

    # ---------- helpers ----------
    def _user(request):
        u = getattr(request.state, "user", None)
        if isinstance(u, dict):
            return u.get("username") or u.get("nama") or "?"
        return "?"

    def _doc_dir(docid, create=False):
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", docid or "")
        if not safe:
            raise Exception("docid tidak valid.")
        d = os.path.join(STUDIO_DIR, safe)
        if create:
            os.makedirs(d, exist_ok=True)
        return d

    def _load_meta(docid):
        p = os.path.join(_doc_dir(docid), "meta.json")
        if not os.path.isfile(p):
            raise Exception("Dokumen tidak ditemukan (mungkin sudah dibersihkan). Unggah ulang.")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def _global_ctx(query):
        try:
            return kctx.build_analysis_context(query or "", max_chars=1200) or ""
        except Exception:
            return ""

    # ---------- ingest ----------
    def _ingest(data, filename, owner):
        if len(data) > MAX_STUDIO_BYTES:
            return {"ok": False, "error": "Ukuran file melebihi 25 MB."}
        res = dstudio.extract(data, filename)
        if not res.get("ok"):
            return res
        docid = os.urandom(8).hex()
        d = _doc_dir(docid, create=True)
        with open(os.path.join(d, "text.txt"), "w", encoding="utf-8") as f:
            f.write(res["text"])
        with open(os.path.join(d, "tables.json"), "w", encoding="utf-8") as f:
            json.dump(res["tables"], f, ensure_ascii=False)
        tables_meta = [{"name": t["name"], "columns": t["columns"], "n_rows": len(t["rows"])}
                       for t in res["tables"]]
        meta = {
            "docid": docid, "filename": filename, "ext": res["ext"],
            "n_chars": res["n_chars"], "pages": res["pages"], "note": res.get("note", ""),
            "owner": owner, "at": _dt.datetime.now().isoformat(), "tables_meta": tables_meta,
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return {
            "ok": True, "docid": docid, "filename": filename, "ext": res["ext"],
            "n_chars": res["n_chars"], "pages": res["pages"], "note": res.get("note", ""),
            "tables": tables_meta, "preview": res["text"][:dstudio.PREVIEW_CHARS],
        }

    # ---------- LLM map-reduce ----------
    def _llm(output_type, doc_text, question, filename):
        # Masking PII sebelum teks APA PUN dikirim ke LLM cloud (Fase A).
        doc_text = pii_mask.mask_text(doc_text)
        question = pii_mask.mask_text(question)
        gctx = pii_mask.mask_text(_global_ctx(question or filename))
        chunks = dstudio.chunk_text(doc_text)
        if len(chunks) <= 1:
            prompt = dstudio.single_prompt(output_type, doc_text, question, gctx, filename)
            return llm_client.chat([{"role": "user", "content": prompt}],
                                   system=dstudio.GUARDRAIL, max_new_tokens=1500, temperature=0.2)
        partials = []
        for ch in chunks[:MAP_CHUNK_LIMIT]:
            r = llm_client.chat([{"role": "user", "content": dstudio.map_prompt(ch, question)}],
                                system=dstudio.GUARDRAIL, max_new_tokens=500, temperature=0.1)
            partials.append(r)
        rp = dstudio.reduce_prompt(output_type, partials, question, gctx, filename)
        return llm_client.chat([{"role": "user", "content": rp}],
                               system=dstudio.GUARDRAIL, max_new_tokens=1700, temperature=0.2)

    # ---------- generate ----------
    def _generate(docid, output_type, question, request):
        meta = _load_meta(docid)
        d = _doc_dir(docid)
        with open(os.path.join(d, "text.txt"), "r", encoding="utf-8") as f:
            doc_text = f.read()
        tables = []
        tp = os.path.join(d, "tables.json")
        if os.path.isfile(tp):
            with open(tp, "r", encoding="utf-8") as f:
                tables = json.load(f)
        filename = meta.get("filename", "")
        out = {"ok": True, "output": output_type}

        if output_type == "tabel":
            tbl = dstudio.pick_largest_table(tables)
            if tbl and tbl.get("columns"):
                cols, rows, source = tbl["columns"], tbl["rows"], "dokumen"
            else:
                cols, rows = dstudio.parse_table_json(_llm("tabel", doc_text, question, filename))
                source = "ekstraksi AI"
            if not cols:
                return {"ok": False, "error": "Tidak menemukan data tabular di dokumen ini."}
            with open(os.path.join(d, "tabel.xlsx"), "wb") as f:
                f.write(dstudio.table_to_xlsx_bytes(cols, rows, sheet="Data"))
            with open(os.path.join(d, "tabel.csv"), "wb") as f:
                f.write(dstudio.table_to_csv_bytes(cols, rows))
            out.update({
                "html": dstudio.table_to_html(cols, rows, limit=200),
                "n_rows": len(rows), "n_cols": len(cols), "source": source,
                "downloads": [
                    {"f": "tabel.xlsx", "name": "%s - tabel.xlsx" % filename, "label": "Unduh XLSX"},
                    {"f": "tabel.csv", "name": "%s - tabel.csv" % filename, "label": "Unduh CSV"},
                ],
            })
            return out

        raw = _llm(output_type, doc_text, question, filename)

        if output_type == "mindmap":
            mermaid = dstudio.outline_to_mermaid(raw)
            with open(os.path.join(d, "mindmap.mmd"), "w", encoding="utf-8") as f:
                f.write(mermaid)
            with open(os.path.join(d, "mindmap.md"), "w", encoding="utf-8") as f:
                f.write(raw)
            out.update({
                "outline": raw, "mermaid": mermaid,
                "downloads": [
                    {"f": "mindmap.mmd", "name": "%s - mindmap.mmd" % filename, "label": "Unduh Mermaid"},
                    {"f": "mindmap.md", "name": "%s - outline.md" % filename, "label": "Unduh Outline"},
                ],
            })
            return out

        fname = "%s.md" % output_type
        with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
            f.write(raw)
        out.update({
            "markdown": raw,
            "downloads": [{"f": fname, "name": "%s - %s.md" % (filename, output_type),
                           "label": "Unduh Markdown"}],
        })
        return out

    # ---------- routes ----------
    @app.get("/studio")
    async def studio_page(request: Request):
        return render_page(request, "studio.html", "studio")

    @app.post("/api/studio/upload")
    async def api_studio_upload(request: Request):
        try:
            form = await request.form()
        except Exception as e:
            return JSONResponse({"ok": False, "error": "Gagal membaca unggahan: %s" % e})
        up = form.get("file")
        if not isinstance(up, StarletteUploadFile):
            return JSONResponse({"ok": False, "error": "Tidak ada file diunggah."})
        data = await up.read()
        filename = up.filename or "dokumen"
        owner = _user(request)
        try:
            return JSONResponse(await run_in_threadpool(_ingest, data, filename, owner))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/studio/generate")
    async def api_studio_generate(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        docid = (body.get("docid") or "").strip()
        output_type = (body.get("output") or "ringkasan").strip().lower()
        question = (body.get("question") or "").strip()
        if not docid:
            return JSONResponse({"ok": False, "error": "Belum ada dokumen. Unggah dulu."})
        if output_type not in dstudio.OUTPUT_TYPES:
            return JSONResponse({"ok": False, "error": "Jenis keluaran tidak dikenal."})
        try:
            return JSONResponse(await run_in_threadpool(_generate, docid, output_type, question, request))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/studio/download")
    async def api_studio_download(request: Request):
        q = request.query_params
        docid = (q.get("docid") or "").strip()
        fkey = (q.get("f") or "").strip()
        dlname = (q.get("name") or fkey).strip()
        if not re.match(r"^[A-Za-z0-9_.\-]+$", fkey or ""):
            return PlainTextResponse("Nama file tidak valid.", status_code=400)
        try:
            d = _doc_dir(docid)
        except Exception as e:
            return PlainTextResponse(str(e), status_code=400)
        p = os.path.join(d, fkey)
        if not os.path.isfile(p):
            return PlainTextResponse("File tidak ditemukan.", status_code=404)
        with open(p, "rb") as f:
            data = f.read()
        ext = os.path.splitext(fkey)[1].lstrip(".").lower()
        mime = {
            "xlsx": xlsx_mime, "csv": "text/csv; charset=utf-8",
            "md": "text/markdown; charset=utf-8", "mmd": "text/plain; charset=utf-8",
        }.get(ext, "application/octet-stream")
        headers = {"Content-Disposition": 'attachment; filename="%s"' % dlname.replace('"', "")}
        return Response(content=data, media_type=mime, headers=headers)
