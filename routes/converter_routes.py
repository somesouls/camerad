# -*- coding: utf-8 -*-
"""routes/converter_routes.py — Endpoint HTTP untuk menu Converter Dokumen & File.

Menyediakan:
- Halaman /converter (Accordion Umum)
- API konversi Excel <-> CSV
- API penggabungan (merge) & pemisahan (split) PDF
"""
import re
from typing import Optional

from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool
from fastapi import Request
from fastapi.responses import JSONResponse, Response

import converter.engine as engine

MAX_CONVERTER_BYTES = 50 * 1024 * 1024  # 50 MB


def register(app, *, render_page):
    """Mendaftarkan route halaman dan API converter ke aplikasi FastAPI."""

    # ---------- HALAMAN UTAMA ----------
    @app.get("/converter")
    async def converter_page(request: Request):
        return render_page(request, "converter.html", "converter")

    # ---------- HELPER BACA FILE ----------
    async def _read_upload(up) -> bytes:
        if not isinstance(up, StarletteUploadFile):
            raise ValueError("Berkas tidak valid atau tidak terunggah.")
        content = await up.read()
        if len(content) > MAX_CONVERTER_BYTES:
            raise ValueError("Ukuran berkas melebihi batas maksimum 50 MB.")
        if len(content) == 0:
            raise ValueError("Berkas yang diunggah kosong.")
        return content

    # ---------- 1. EXCEL KE CSV ----------
    @app.post("/api/converter/excel/inspect")
    async def api_excel_inspect(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)
            info = await run_in_threadpool(engine.inspect_excel, content)
            return JSONResponse({"ok": True, "filename": up.filename, "info": info})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/converter/excel/convert")
    async def api_excel_convert(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            sheet_name = str(form.get("sheet_name") or "").strip() or None
            delimiter = str(form.get("delimiter") or ",")
            encoding = str(form.get("encoding") or "utf-8-sig")

            # Sanitasi delimiter
            del_map = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}
            eff_del = del_map.get(delimiter, delimiter)

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.excel_to_csv,
                content,
                sheet_name=sheet_name,
                delimiter=eff_del,
                encoding=encoding,
            )

            # Jika nama berkas asli ada, gunakan sebagai prefix
            if up.filename:
                base = re.sub(r"\.[^.]+$", "", up.filename)
                if sheet_name and sheet_name != "__all__":
                    fname = f"{base}_{fname}"
                elif sheet_name == "__all__":
                    fname = f"{base}_semua_sheet.zip"

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Converter-Meta": "1",
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 2. CSV KE EXCEL ----------
    @app.post("/api/converter/csv/inspect")
    async def api_csv_inspect(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)
            info = await run_in_threadpool(engine.inspect_csv, content)
            return JSONResponse({"ok": True, "filename": up.filename, "info": info})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/converter/csv/convert")
    async def api_csv_convert(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            delimiter = str(form.get("delimiter") or "auto")
            encoding = str(form.get("encoding") or "auto")
            sheet_name = str(form.get("sheet_name") or "Data").strip() or "Data"
            preserve_text = str(form.get("preserve_text", "1")).lower() in ("1", "true", "yes")

            del_map = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", "auto": "auto"}
            eff_del = del_map.get(delimiter, delimiter)

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.csv_to_excel,
                content,
                delimiter=eff_del,
                encoding=encoding,
                sheet_name=sheet_name,
                preserve_text=preserve_text,
            )

            if up.filename:
                base = re.sub(r"\.[^.]+$", "", up.filename)
                fname = f"{base}.xlsx"

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 3. GABUNG PDF (MERGE) ----------
    @app.post("/api/converter/pdf/inspect")
    async def api_pdf_inspect(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)
            info = await run_in_threadpool(engine.inspect_pdf, content)
            return JSONResponse({"ok": True, "filename": up.filename, "info": info})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/converter/pdf/merge")
    async def api_pdf_merge(request: Request):
        try:
            form = await request.form()
            files_up = form.getlist("files")
            if not files_up:
                return JSONResponse({"ok": False, "error": "Pilih minimal 2 berkas PDF untuk digabungkan."}, status_code=400)

            pdf_list = []
            for up in files_up:
                content = await _read_upload(up)
                pdf_list.append((up.filename or "dokumen.pdf", content))

            custom_name = str(form.get("output_name") or "").strip()
            if custom_name and not custom_name.lower().endswith(".pdf"):
                custom_name += ".pdf"

            out_bytes, default_name, mime, meta = await run_in_threadpool(engine.merge_pdfs, pdf_list)
            fname = custom_name or default_name

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 4. PISAH PDF (SPLIT) ----------
    @app.post("/api/converter/pdf/split")
    async def api_pdf_split(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            mode = str(form.get("mode") or "all").strip().lower()
            range_str = str(form.get("range_str") or "").strip()
            step_n = int(form.get("step_n") or 1)
            merge_range = str(form.get("merge_range", "1")).lower() in ("1", "true", "yes")
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.split_pdf,
                content,
                mode=mode,
                range_str=range_str,
                step_n=step_n,
                merge_range_result=merge_range,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

