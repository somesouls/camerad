# -*- coding: utf-8 -*-
"""routes/converter_routes.py — Endpoint HTTP untuk menu Converter Dokumen & File.

Menyediakan:
- Halaman /converter (Accordion Umum)
- API konversi Excel <-> CSV
- API penggabungan (merge) & pemisahan (split) PDF
"""
import re
import json
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

    # ---------- 2B. CSV KE CSV ----------
    @app.post("/api/converter/csv-to-csv")
    async def api_csv_to_csv(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            src_delimiter = str(form.get("src_delimiter") or "auto")
            target_delimiter = str(form.get("target_delimiter") or ",")
            src_encoding = str(form.get("src_encoding") or "auto")
            target_encoding = str(form.get("target_encoding") or "utf-8-sig")
            quoting = str(form.get("quoting") or "minimal")
            trim_whitespace = str(form.get("trim_whitespace", "0")).lower() in ("1", "true", "yes")
            remove_empty_rows = str(form.get("remove_empty_rows", "1")).lower() in ("1", "true", "yes")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.csv_to_csv,
                content,
                src_delimiter=src_delimiter,
                target_delimiter=target_delimiter,
                src_encoding=src_encoding,
                target_encoding=target_encoding,
                quoting=quoting,
                trim_whitespace=trim_whitespace,
                remove_empty_rows=remove_empty_rows,
            )

            if up.filename:
                base = re.sub(r"\.[^.]+$", "", up.filename)
                fname = f"{base}_reformat.csv"

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

    @app.post("/api/converter/pdf/thumbnails")
    async def api_pdf_thumbnails(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)
            try:
                max_pages = int(form.get("max_pages") or 60)
            except (ValueError, TypeError):
                max_pages = 60
            result = await run_in_threadpool(engine.generate_pdf_thumbnails, content, max_pages=max_pages)
            return JSONResponse({"ok": True, **result})
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

    # ---------- 5. GAMBAR KE PDF (IMAGES TO PDF) ----------
    @app.post("/api/converter/images-to-pdf")
    async def api_images_to_pdf(request: Request):
        try:
            form = await request.form()
            files_up = form.getlist("files")
            if not files_up:
                return JSONResponse({"ok": False, "error": "Pilih minimal 1 berkas gambar untuk dikonversi."}, status_code=400)

            img_list = []
            for up in files_up:
                content = await _read_upload(up)
                img_list.append((up.filename or "gambar.jpg", content))

            page_size = str(form.get("page_size") or "A4").strip()
            orientation = str(form.get("orientation") or "auto").strip().lower()
            try:
                margin_mm = int(form.get("margin_mm") or 10)
            except (ValueError, TypeError):
                margin_mm = 10
            custom_name = str(form.get("output_name") or "dokumen_gambar.pdf").strip()

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.images_to_pdf,
                img_list,
                page_size=page_size,
                orientation=orientation,
                margin_mm=margin_mm,
                output_name=custom_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 6. PDF KE GAMBAR (PDF TO IMAGES) ----------
    @app.post("/api/converter/pdf-to-images")
    async def api_pdf_to_images(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            try:
                dpi = int(form.get("dpi") or 150)
            except (ValueError, TypeError):
                dpi = 150
            img_format = str(form.get("format") or "jpeg").strip().lower()
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.pdf_to_images,
                content,
                dpi=dpi,
                img_format=img_format,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 7. KOMPRES PDF (COMPRESS PDF) ----------
    @app.post("/api/converter/pdf/compress")
    async def api_pdf_compress(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            level = str(form.get("level") or "medium").strip().lower()
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.compress_pdf,
                content,
                level=level,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Original-Size": str(meta.get("original_size", 0)),
                "X-Compressed-Size": str(meta.get("compressed_size", 0)),
                "X-Saved-Percent": str(meta.get("saved_percent", 0)),
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 8. PUTAR PDF (ROTATE PDF) ----------
    @app.post("/api/converter/pdf/rotate")
    async def api_pdf_rotate(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            try:
                rotation = int(form.get("rotation") or 90)
            except (ValueError, TypeError):
                rotation = 90
            pages_mode = str(form.get("pages_mode") or "all").strip().lower()
            range_str = str(form.get("range_str") or "").strip()
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            rotations_json = form.get("rotations_json")
            rotations_map = None
            if rotations_json:
                try:
                    rotations_map = json.loads(rotations_json)
                except Exception:
                    rotations_map = None

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.rotate_pdf,
                content,
                rotation=rotation,
                pages_mode=pages_mode,
                range_str=range_str,
                rotations_map=rotations_map,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 9. WATERMARK / CAP PDF (STAMP PDF) ----------
    @app.post("/api/converter/pdf/watermark")
    async def api_pdf_watermark(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            text = str(form.get("text") or "SALINAN").strip()
            try:
                opacity = float(form.get("opacity") or 0.25)
            except (ValueError, TypeError):
                opacity = 0.25
            try:
                font_size = int(form.get("font_size") or 44)
            except (ValueError, TypeError):
                font_size = 44
            color = str(form.get("color") or "gray").strip().lower()
            try:
                rotation_deg = int(form.get("rotation_deg") or 45)
            except (ValueError, TypeError):
                rotation_deg = 45
            pages_mode = str(form.get("pages_mode") or "all").strip().lower()
            range_str = str(form.get("range_str") or "").strip()
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.watermark_pdf,
                content,
                text=text,
                opacity=opacity,
                font_size=font_size,
                color=color,
                rotation_deg=rotation_deg,
                pages_mode=pages_mode,
                range_str=range_str,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ---------- 10. HAPUS HALAMAN PDF (DELETE PAGES) ----------
    @app.post("/api/converter/pdf/delete-pages")
    async def api_pdf_delete_pages(request: Request):
        try:
            form = await request.form()
            up = form.get("file")
            content = await _read_upload(up)

            pages_to_delete = str(form.get("pages_to_delete") or "").strip()
            base_name = re.sub(r"\.[^.]+$", "", up.filename or "dokumen")

            out_bytes, fname, mime, meta = await run_in_threadpool(
                engine.delete_pages_pdf,
                content,
                pages_to_delete=pages_to_delete,
                base_filename=base_name,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{fname}"',
            }
            return Response(content=out_bytes, media_type=mime, headers=headers)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

