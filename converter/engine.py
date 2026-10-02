# -*- coding: utf-8 -*-
"""converter/engine.py — Logika inti konversi berkas (Excel, CSV, PDF).

Stdlib + openpyxl + pypdf.
Murni in-memory (io.BytesIO), tanpa file residual dan tanpa dependensi eksternal cloud.
"""
import io
import re
import csv
import zipfile
import datetime
from typing import List, Tuple, Dict, Any, Optional

import openpyxl
import pypdf


# ==============================================================================
# 1. EXCEL -> CSV
# ==============================================================================

def inspect_excel(data: bytes) -> Dict[str, Any]:
    """Membaca metadata buku kerja Excel: daftar sheet, perkiraan baris/kolom, dan pratinjau."""
    buf = io.BytesIO(data)
    try:
        wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Gagal membaca berkas Excel: {e}")

    sheets_info = []
    for sname in wb.sheetnames:
        sheet = wb[sname]
        preview_rows = []
        row_count = 0
        max_cols = 0
        try:
            for row in sheet.iter_rows(values_only=True):
                # Abaikan baris kosong di awal/akhir
                cells = ["" if c is None else str(c) for c in row]
                if not any(cells):
                    continue
                row_count += 1
                if len(cells) > max_cols:
                    max_cols = len(cells)
                if len(preview_rows) < 8:
                    preview_rows.append(cells[:15])  # batasi 15 kolom pratinjau
        except Exception:
            pass

        sheets_info.append({
            "name": sname,
            "estimated_rows": row_count,
            "estimated_cols": max_cols,
            "preview": preview_rows,
        })
    wb.close()

    return {
        "sheets": sheets_info,
        "total_sheets": len(sheets_info),
        "sheet_names": [s["name"] for s in sheets_info],
    }


def _format_cell_value(val: Any) -> str:
    """Format nilai sel Excel ke representasi teks yang konsisten untuk CSV."""
    if val is None:
        return ""
    if isinstance(val, (datetime.datetime, datetime.date)):
        if isinstance(val, datetime.datetime) and (val.hour or val.minute or val.second):
            return val.strftime("%Y-%m-%d %H:%M:%S")
        return val.strftime("%Y-%m-%d")
    return str(val)


def _sheet_to_csv_text(sheet, delimiter: str = ",") -> Tuple[str, List[List[str]], int]:
    """Mengekstrak sheet openpyxl menjadi teks CSV dan baris pratinjau."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    preview = []
    total_rows = 0

    for row in sheet.iter_rows(values_only=True):
        formatted = [_format_cell_value(c) for c in row]
        # Skip jika baris benar-benar kosong
        if not any(formatted):
            continue
        writer.writerow(formatted)
        total_rows += 1
        if len(preview) < 10:
            preview.append(formatted[:15])

    return output.getvalue(), preview, total_rows


def excel_to_csv(
    data: bytes,
    sheet_name: Optional[str] = None,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Mengonversi berkas Excel (.xlsx / .xls) menjadi CSV atau ZIP (jika semua sheet).

    Returns:
        (content_bytes, filename, mime_type, meta_dict)
    """
    buf = io.BytesIO(data)
    try:
        wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas Excel: {e}")

    names = wb.sheetnames
    if not names:
        wb.close()
        raise ValueError("Berkas Excel tidak memiliki sheet yang dapat dibaca.")

    # Pilihan: Semua Sheet -> dijadikan ZIP
    if sheet_name == "__all__":
        zip_buf = io.BytesIO()
        preview_all = {}
        total_sheets_done = 0
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for sname in names:
                sheet = wb[sname]
                csv_text, preview, count = _sheet_to_csv_text(sheet, delimiter=delimiter)
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", sname).strip() or f"sheet_{total_sheets_done+1}"
                zf.writestr(f"{safe_name}.csv", csv_text.encode(encoding))
                preview_all[sname] = {"rows": count, "preview": preview}
                total_sheets_done += 1
        wb.close()
        return (
            zip_buf.getvalue(),
            "excel_all_sheets.zip",
            "application/zip",
            {"mode": "all", "sheets": preview_all, "total_sheets": total_sheets_done},
        )

    # Konversi 1 Sheet tertentu atau Sheet pertama
    target_name = sheet_name if (sheet_name and sheet_name in names) else names[0]
    sheet = wb[target_name]
    csv_text, preview, total_rows = _sheet_to_csv_text(sheet, delimiter=delimiter)
    wb.close()

    safe_name = re.sub(r'[\\/*?:"<>|]', "_", target_name).strip() or "data"
    csv_bytes = csv_text.encode(encoding)

    return (
        csv_bytes,
        f"{safe_name}.csv",
        f"text/csv; charset={encoding}",
        {
            "mode": "single",
            "sheet_name": target_name,
            "total_rows": total_rows,
            "preview": preview,
        },
    )


# ==============================================================================
# 2. CSV -> EXCEL
# ==============================================================================

def detect_csv_format(sample_bytes: bytes) -> Tuple[str, str]:
    """Mendeteksi encoding dan delimiter berkas CSV secara heuristik."""
    # Deteksi encoding
    encoding = "utf-8"
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            sample_bytes.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    text_sample = sample_bytes[:16384].decode(encoding, errors="replace")

    # Deteksi delimiter
    delimiters = [",", ";", "\t", "|"]
    counts = {d: text_sample.count(d) for d in delimiters}
    # Utamakan delimiter yang paling sering muncul
    best_del = max(counts, key=counts.get)
    if counts[best_del] == 0:
        best_del = ","

    return encoding, best_del


def inspect_csv(data: bytes) -> Dict[str, Any]:
    """Membaca cuplikan CSV untuk deteksi delimiter, encoding, dan pratinjau data."""
    enc, delimiter = detect_csv_format(data)
    text = data.decode(enc, errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    preview_rows = []
    count = 0
    for row in reader:
        count += 1
        if len(preview_rows) < 8:
            preview_rows.append(row[:15])

    return {
        "detected_encoding": enc,
        "detected_delimiter": delimiter,
        "estimated_rows": count,
        "preview": preview_rows,
    }


def csv_to_excel(
    data: bytes,
    delimiter: str = "auto",
    encoding: str = "auto",
    sheet_name: str = "Data",
    preserve_text: bool = True,
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Mengonversi berkas CSV menjadi format Excel .xlsx.

    Jika preserve_text=True: angka dengan awalan '0' (seperti NPWP, NIK, No. HP)
    tetap dipertahankan sebagai teks agar nol di depan tidak hilang.
    """
    detected_enc, detected_del = detect_csv_format(data)
    eff_enc = detected_enc if (not encoding or encoding == "auto") else encoding
    eff_del = detected_del if (not delimiter or delimiter == "auto") else delimiter

    try:
        text = data.decode(eff_enc, errors="replace")
    except Exception:
        text = data.decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(text), delimiter=eff_del)

    wb = openpyxl.Workbook()
    ws = wb.active
    safe_sheet = re.sub(r'[\\/*?:"<>|]', "_", sheet_name or "Data")[:31]
    ws.title = safe_sheet

    preview = []
    total_rows = 0

    for row in reader:
        parsed_row = []
        for cell in row:
            if not cell:
                parsed_row.append("")
                continue
            s_val = cell.strip()
            # Cek preserve text: angka dengan awalan 0 (panjang > 1, misal "012345")
            if preserve_text and len(s_val) > 1 and s_val.startswith("0") and s_val.isdigit():
                parsed_row.append(s_val)
            elif re.match(r"^-?\d+$", s_val):
                try:
                    parsed_row.append(int(s_val))
                except Exception:
                    parsed_row.append(s_val)
            elif re.match(r"^-?\d+\.\d+$", s_val):
                try:
                    parsed_row.append(float(s_val))
                except Exception:
                    parsed_row.append(s_val)
            else:
                parsed_row.append(cell)

        ws.append(parsed_row)
        total_rows += 1
        if len(preview) < 10:
            preview.append([str(c) for c in parsed_row[:15]])

    out_buf = io.BytesIO()
    wb.save(out_buf)
    wb.close()

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return (
        out_buf.getvalue(),
        "converted_data.xlsx",
        xlsx_mime,
        {
            "delimiter_used": eff_del,
            "encoding_used": eff_enc,
            "total_rows": total_rows,
            "preview": preview,
        },
    )


# ==============================================================================
# 3. PDF MERGE
# ==============================================================================

def inspect_pdf(data: bytes) -> Dict[str, Any]:
    """Membaca informasi dasar berkas PDF (jumlah halaman, enkripsi, judul)."""
    buf = io.BytesIO(data)
    try:
        reader = pypdf.PdfReader(buf)
        encrypted = reader.is_encrypted
        page_count = len(reader.pages) if not encrypted else 0
        meta = reader.metadata or {}
        title = meta.get("/Title") or ""
    except Exception as e:
        raise ValueError(f"Format PDF tidak valid atau rusak: {e}")

    return {
        "page_count": page_count,
        "is_encrypted": encrypted,
        "title": str(title),
        "size_bytes": len(data),
    }


def merge_pdfs(files: List[Tuple[str, bytes]]) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Menggabungkan beberapa berkas PDF secara berurutan menjadi satu berkas PDF."""
    if not files:
        raise ValueError("Tidak ada berkas PDF yang diberikan untuk digabungkan.")

    merger = pypdf.PdfWriter()
    total_pages = 0
    file_details = []

    for name, content in files:
        buf = io.BytesIO(content)
        try:
            reader = pypdf.PdfReader(buf)
            if reader.is_encrypted:
                raise ValueError(f"Berkas '{name}' dilindungi kata sandi. Harap buka kuncinya terlebih dahulu.")
            cnt = len(reader.pages)
            merger.append(reader)
            total_pages += cnt
            file_details.append({"filename": name, "pages": cnt, "size": len(content)})
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Gagal membaca berkas '{name}': {e}")

    out_buf = io.BytesIO()
    merger.write(out_buf)

    return (
        out_buf.getvalue(),
        "dokumen_gabungan.pdf",
        "application/pdf",
        {
            "total_files": len(files),
            "total_pages": total_pages,
            "files": file_details,
        },
    )


# ==============================================================================
# 4. PDF SPLIT
# ==============================================================================

def parse_page_ranges(range_str: str, max_pages: int) -> List[int]:
    """Mem-parsing string rentang halaman (misal: '1-3, 5, 8-10') menjadi daftar nomor halaman 1-indexed."""
    if not range_str or not range_str.strip():
        raise ValueError("Rentang halaman tidak boleh kosong.")

    parts = [p.strip() for p in range_str.split(",") if p.strip()]
    pages = []
    seen = set()

    for part in parts:
        if "-" in part:
            chunks = part.split("-", 1)
            try:
                start = int(chunks[0].strip())
                end = int(chunks[1].strip())
            except ValueError:
                raise ValueError(f"Format rentang '{part}' tidak valid. Gunakan angka, misal '1-5'.")
            if start > end:
                raise ValueError(f"Rentang '{part}' terbalik (halaman awal {start} > akhir {end}).")
            if start < 1 or end > max_pages:
                raise ValueError(f"Rentang '{part}' di luar jumlah halaman dokumen (1 s.d. {max_pages}).")
            for p in range(start, end + 1):
                if p not in seen:
                    pages.append(p)
                    seen.add(p)
        else:
            try:
                p = int(part)
            except ValueError:
                raise ValueError(f"Nomor halaman '{part}' bukan angka yang valid.")
            if p < 1 or p > max_pages:
                raise ValueError(f"Halaman {p} di luar rentang halaman dokumen (1 s.d. {max_pages}).")
            if p not in seen:
                pages.append(p)
                seen.add(p)

    if not pages:
        raise ValueError("Tidak ada halaman yang dipilih.")

    return pages


def split_pdf(
    data: bytes,
    mode: str = "all",
    range_str: str = "",
    step_n: int = 1,
    merge_range_result: bool = True,
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Memisahkan berkas PDF berdasarkan mode.

    Modes:
    1. 'all': Memisahkan setiap halaman jadi file PDF tersendiri -> Output ZIP.
    2. 'range': Ekstrak rentang halaman tertentu (mis. '1-3, 5').
       - Jika merge_range_result=True -> Output single PDF.
       - Jika merge_range_result=False -> Output ZIP tiap halaman/rentang terpisah.
    3. 'step': Memecah per N halaman (mis. setiap 2 halaman) -> Output ZIP.
    """
    buf = io.BytesIO(data)
    try:
        reader = pypdf.PdfReader(buf)
        if reader.is_encrypted:
            raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kuncinya terlebih dahulu.")
        total_pages = len(reader.pages)
    except Exception as e:
        raise ValueError(f"Gagal membuka PDF: {e}")

    if total_pages < 1:
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"

    # --- Mode 1: Pisahkan Semua Halaman ---
    if mode == "all":
        zip_buf = io.BytesIO()
        pad = max(len(str(total_pages)), 2)
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx in range(total_pages):
                writer = pypdf.PdfWriter()
                writer.add_page(reader.pages[idx])
                p_buf = io.BytesIO()
                writer.write(p_buf)
                zf.writestr(f"{safe_base}_hal_{idx+1:0{pad}d}.pdf", p_buf.getvalue())

        return (
            zip_buf.getvalue(),
            f"{safe_base}_semua_halaman.zip",
            "application/zip",
            {"mode": "all", "total_pages": total_pages, "files_created": total_pages},
        )

    # --- Mode 2: Ekstrak Rentang Tertentu ---
    if mode == "range":
        pages = parse_page_ranges(range_str, total_pages)
        if merge_range_result:
            writer = pypdf.PdfWriter()
            for p in pages:
                writer.add_page(reader.pages[p - 1])
            p_buf = io.BytesIO()
            writer.write(p_buf)
            return (
                p_buf.getvalue(),
                f"{safe_base}_halaman_ekstrak.pdf",
                "application/pdf",
                {"mode": "range", "selected_pages": pages, "total_extracted": len(pages)},
            )
        else:
            zip_buf = io.BytesIO()
            pad = max(len(str(total_pages)), 2)
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in pages:
                    writer = pypdf.PdfWriter()
                    writer.add_page(reader.pages[p - 1])
                    p_buf = io.BytesIO()
                    writer.write(p_buf)
                    zf.writestr(f"{safe_base}_hal_{p:0{pad}d}.pdf", p_buf.getvalue())
            return (
                zip_buf.getvalue(),
                f"{safe_base}_halaman_ekstrak.zip",
                "application/zip",
                {"mode": "range_zip", "selected_pages": pages, "total_extracted": len(pages)},
            )

    # --- Mode 3: Pisah per N Halaman ---
    if mode == "step":
        step = max(int(step_n or 1), 1)
        zip_buf = io.BytesIO()
        part_idx = 1
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for start in range(0, total_pages, step):
                end = min(start + step, total_pages)
                writer = pypdf.PdfWriter()
                for p_idx in range(start, end):
                    writer.add_page(reader.pages[p_idx])
                p_buf = io.BytesIO()
                writer.write(p_buf)
                zf.writestr(f"{safe_base}_bagian_{part_idx:02d}_hal_{start+1}-{end}.pdf", p_buf.getvalue())
                part_idx += 1

        return (
            zip_buf.getvalue(),
            f"{safe_base}_per_{step}_halaman.zip",
            "application/zip",
            {"mode": "step", "step_size": step, "parts_created": part_idx - 1, "total_pages": total_pages},
        )

    raise ValueError(f"Mode pemisahan PDF '{mode}' tidak dikenal.")

