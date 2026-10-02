# -*- coding: utf-8 -*-
"""converter/engine.py — Logika inti konversi berkas (Excel, CSV, PDF).

Mendukung:
- Excel Modern (.xlsx) via openpyxl
- Excel Lama (.xls BIFF8/OLE2) via xlrd
- Berkas Ekspor Tabel HTML (.xls berbasis <table>) via lxml/html
- Berkas Ekspor XML Spreadsheet 2003 (SpreadsheetML)
- CSV <-> Excel (.xlsx) dengan proteksi awalan nol (NPWP/NIK)
- PDF Merge & Split (semua halaman, rentang halaman, atau per N halaman)

Murni in-memory (io.BytesIO), tanpa file residual dan tanpa dependensi eksternal cloud.
"""
import io
import re
import csv
import base64
import zipfile
import datetime
from typing import List, Tuple, Dict, Any, Optional, Iterator

import openpyxl
import pypdf
import fitz
from PIL import Image

try:
    import xlrd
    HAVE_XLRD = True
except ImportError:
    HAVE_XLRD = False

try:
    import lxml.html
    HAVE_LXML = True
except ImportError:
    HAVE_LXML = False

import xml.etree.ElementTree as ET


# ==============================================================================
# UNIVERSAL EXCEL LOADER (.xlsx, .xls, HTML table, XML Spreadsheet)
# ==============================================================================

def _format_cell_value(val: Any) -> str:
    """Format nilai sel openpyxl (.xlsx) ke representasi teks yang konsisten untuk CSV."""
    if val is None:
        return ""
    if isinstance(val, (datetime.datetime, datetime.date)):
        if isinstance(val, datetime.datetime) and (val.hour or val.minute or val.second):
            return val.strftime("%Y-%m-%d %H:%M:%S")
        return val.strftime("%Y-%m-%d")
    return str(val)


def _format_xlrd_cell(cell, book) -> str:
    """Format nilai sel xlrd (.xls) ke string yang konsisten untuk CSV."""
    if cell is None or not HAVE_XLRD:
        return ""
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            dt_tuple = xlrd.xldate_as_tuple(cell.value, book.datemode)
            if dt_tuple[3:] == (0, 0, 0):
                return f"{dt_tuple[0]:04d}-{dt_tuple[1]:02d}-{dt_tuple[2]:02d}"
            return f"{dt_tuple[0]:04d}-{dt_tuple[1]:02d}-{dt_tuple[2]:02d} {dt_tuple[3]:02d}:{dt_tuple[4]:02d}:{dt_tuple[5]:02d}"
        except Exception:
            return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        if isinstance(cell.value, float) and cell.value.is_integer():
            return str(int(cell.value))
        return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return ""
    return str(cell.value)


class ExcelWorkbookWrapper:
    """Pembungkus universal untuk membaca berbagai varian berkas Excel:
    - .xlsx (OpenXML / Zip)
    - .xls (BIFF8 / OLE2 Compound Document)
    - Ekspor HTML table yang disimpan dengan ekstensi .xls / .xlsx
    - Ekspor XML Spreadsheet 2003 (SpreadsheetML)
    """

    def __init__(self, data: bytes):
        self.data = data
        self.wb_openpyxl = None
        self.wb_xlrd = None
        self.static_sheets = {}  # {sheet_name: List[List[str]]}
        self.engine = None
        self._init_engine()

    def _init_engine(self):
        # 1. Deteksi tanda tangan ZIP -> Berkas .xlsx asli
        if self.data.startswith(b"PK\x03\x04"):
            try:
                self.wb_openpyxl = openpyxl.load_workbook(
                    io.BytesIO(self.data), read_only=True, data_only=True
                )
                self.engine = "openpyxl"
                return
            except Exception:
                pass

        # 2. Deteksi tanda tangan OLE2 -> Berkas .xls biner asli (BIFF8)
        if self.data.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
            if HAVE_XLRD:
                try:
                    self.wb_xlrd = xlrd.open_workbook(file_contents=self.data)
                    self.engine = "xlrd"
                    return
                except Exception as e:
                    raise ValueError(f"Gagal membaca berkas Excel .xls: {e}")
            else:
                raise ValueError("Berkas terdeteksi sebagai Excel versi lama (.xls). Pustaka 'xlrd' dibutuhkan di server.")

        # 3. Deteksi berkas HTML yang disimpan sebagai .xls/.xlsx (sangat umum di aplikasi web/laporan)
        sample = self.data[:4096].lower()
        if b"<html" in sample or b"<table" in sample or b"<!doctype html" in sample:
            if self._try_parse_html():
                return

        # 4. Deteksi berkas XML Spreadsheet 2003 (SpreadsheetML)
        if b"urn:schemas-microsoft-com:office:spreadsheet" in sample:
            if self._try_parse_xml_spreadsheet():
                return

        # 5. Fallback ke xlrd jika data mungkin OLE2 tanpa header standar
        if HAVE_XLRD:
            try:
                self.wb_xlrd = xlrd.open_workbook(file_contents=self.data)
                self.engine = "xlrd"
                return
            except Exception:
                pass

        # 6. Fallback ke openpyxl
        try:
            self.wb_openpyxl = openpyxl.load_workbook(
                io.BytesIO(self.data), read_only=True, data_only=True
            )
            self.engine = "openpyxl"
            return
        except Exception:
            pass

        # 7. Fallback deteksi teks berpemisah (CSV/TSV yang diubah ekstensinya jadi .xlsx/.xls)
        if self._try_parse_csv_text():
            return

        raise ValueError(
            "Berkas bukan berkas Excel (.xlsx / .xls) yang valid atau berkas mengalami kerusakan."
        )

    def _try_parse_html(self) -> bool:
        """Membaca tabel HTML dan mengubahnya menjadi lembar kerja virtual."""
        if not HAVE_LXML:
            return False
        try:
            doc = lxml.html.fromstring(self.data)
            tables = doc.xpath("//table")
            if not tables:
                return False
            sheets = {}
            for i, tbl in enumerate(tables, 1):
                name = f"Tabel_{i}"
                rows = []
                for tr in tbl.xpath(".//tr"):
                    cells = [td.text_content().strip() for td in tr.xpath("./th|./td")]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    sheets[name] = rows
            if sheets:
                self.static_sheets = sheets
                self.engine = "html"
                return True
        except Exception:
            pass
        return False

    def _try_parse_xml_spreadsheet(self) -> bool:
        """Membaca format XML Spreadsheet 2003."""
        try:
            root = ET.fromstring(self.data)
            sheets = {}
            for ws in root.findall(".//{urn:schemas-microsoft-com:office:spreadsheet}Worksheet"):
                sname = ws.attrib.get("{urn:schemas-microsoft-com:office:spreadsheet}Name", "Sheet1")
                rows = []
                for row in ws.findall(".//{urn:schemas-microsoft-com:office:spreadsheet}Row"):
                    cells = []
                    for cell in row.findall(".//{urn:schemas-microsoft-com:office:spreadsheet}Cell"):
                        data_el = cell.find("{urn:schemas-microsoft-com:office:spreadsheet}Data")
                        cells.append(
                            data_el.text if (data_el is not None and data_el.text is not None) else ""
                        )
                    if any(cells):
                        rows.append(cells)
                if rows:
                    sheets[sname] = rows
            if sheets:
                self.static_sheets = sheets
                self.engine = "xml"
                return True
        except Exception:
            pass
        return False

    def _try_parse_csv_text(self) -> bool:
        """Mendeteksi apakah berkas sebenarnya adalah teks CSV yang dinamai .xls/.xlsx."""
        try:
            for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    text = self.data.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = None
            if not text:
                return False
            lines = text.strip().splitlines()
            if len(lines) < 2:
                return False
            # Cek ada pemisah umum
            first = lines[0]
            if not any(d in first for d in (",", ";", "\t", "|")):
                return False
            reader = csv.reader(io.StringIO(text), delimiter="," if "," in first else ";")
            rows = [r for r in reader if any(r)]
            if rows and len(rows[0]) > 1:
                self.static_sheets = {"Data": rows}
                self.engine = "csv"
                return True
        except Exception:
            pass
        return False

    @property
    def sheet_names(self) -> List[str]:
        if self.engine == "openpyxl":
            return self.wb_openpyxl.sheetnames
        elif self.engine == "xlrd":
            return self.wb_xlrd.sheet_names()
        elif self.engine in ("html", "xml", "csv"):
            return list(self.static_sheets.keys())
        return []

    def iter_sheet_rows(self, sheet_name: str) -> Iterator[List[str]]:
        if self.engine == "openpyxl":
            sheet = self.wb_openpyxl[sheet_name]
            for row in sheet.iter_rows(values_only=True):
                yield [_format_cell_value(c) for c in row]
        elif self.engine == "xlrd":
            sheet = self.wb_xlrd.sheet_by_name(sheet_name)
            for r_idx in range(sheet.nrows):
                row_cells = [sheet.cell(r_idx, c_idx) for c_idx in range(sheet.ncols)]
                yield [_format_xlrd_cell(c, self.wb_xlrd) for c in row_cells]
        elif self.engine in ("html", "xml", "csv"):
            for row in self.static_sheets.get(sheet_name, []):
                yield [str(c) if c is not None else "" for c in row]

    def close(self):
        if self.wb_openpyxl:
            try:
                self.wb_openpyxl.close()
            except Exception:
                pass


# ==============================================================================
# 1. EXCEL -> CSV
# ==============================================================================

def inspect_excel(data: bytes) -> Dict[str, Any]:
    """Membaca metadata buku kerja Excel: daftar sheet, perkiraan baris/kolom, dan pratinjau."""
    try:
        wrapper = ExcelWorkbookWrapper(data)
    except Exception as e:
        raise ValueError(f"Gagal membaca berkas Excel: {e}")

    sheets_info = []
    for sname in wrapper.sheet_names:
        preview_rows = []
        row_count = 0
        max_cols = 0
        try:
            for row in wrapper.iter_sheet_rows(sname):
                # Abaikan baris kosong di awal/akhir
                if not any(row):
                    continue
                row_count += 1
                if len(row) > max_cols:
                    max_cols = len(row)
                if len(preview_rows) < 8:
                    preview_rows.append(row[:15])  # batasi 15 kolom pratinjau
        except Exception:
            pass

        sheets_info.append({
            "name": sname,
            "estimated_rows": row_count,
            "estimated_cols": max_cols,
            "preview": preview_rows,
        })
    wrapper.close()

    if not sheets_info:
        raise ValueError("Berkas Excel tidak memiliki baris data atau lembar kerja yang dapat dibaca.")

    return {
        "sheets": sheets_info,
        "total_sheets": len(sheets_info),
        "sheet_names": [s["name"] for s in sheets_info],
        "engine": wrapper.engine,
    }


def _extract_sheet_csv(wrapper: ExcelWorkbookWrapper, sheet_name: str, delimiter: str = ",") -> Tuple[str, List[List[str]], int]:
    """Mengekstrak baris sheet menjadi teks CSV dan baris pratinjau."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    preview = []
    total_rows = 0

    for row in wrapper.iter_sheet_rows(sheet_name):
        if not any(row):
            continue
        writer.writerow(row)
        total_rows += 1
        if len(preview) < 10:
            preview.append(row[:15])

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
    try:
        wrapper = ExcelWorkbookWrapper(data)
    except Exception as e:
        raise ValueError(f"Gagal membaca berkas Excel: {e}")

    names = wrapper.sheet_names
    if not names:
        wrapper.close()
        raise ValueError("Berkas Excel tidak memiliki sheet yang dapat dibaca.")

    # Pilihan: Semua Sheet -> dijadikan ZIP
    if sheet_name == "__all__":
        zip_buf = io.BytesIO()
        preview_all = {}
        total_sheets_done = 0
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for sname in names:
                csv_text, preview, count = _extract_sheet_csv(wrapper, sname, delimiter=delimiter)
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", sname).strip() or f"sheet_{total_sheets_done+1}"
                zf.writestr(f"{safe_name}.csv", csv_text.encode(encoding))
                preview_all[sname] = {"rows": count, "preview": preview}
                total_sheets_done += 1
        wrapper.close()
        return (
            zip_buf.getvalue(),
            "excel_all_sheets.zip",
            "application/zip",
            {"mode": "all", "sheets": preview_all, "total_sheets": total_sheets_done},
        )

    # Konversi 1 Sheet tertentu atau Sheet pertama
    target_name = sheet_name if (sheet_name and sheet_name in names) else names[0]
    csv_text, preview, total_rows = _extract_sheet_csv(wrapper, target_name, delimiter=delimiter)
    wrapper.close()

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
# 2B. CSV -> CSV (Ubah Pemisah & Encoding)
# ==============================================================================

def csv_to_csv(
    data: bytes,
    src_delimiter: str = "auto",
    target_delimiter: str = ",",
    src_encoding: str = "auto",
    target_encoding: str = "utf-8-sig",
    quoting: str = "minimal",
    trim_whitespace: bool = False,
    remove_empty_rows: bool = True,
    line_terminator: str = "\r\n",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Mengubah format berkas CSV: pemisah (delimiter), encoding, dan tanda kutip.

    Misal: mengubah Titik Koma (;) ke Koma (,), atau Koma ke Semicolon,
    mengubah encoding ke UTF-8 BOM untuk Excel atau UTF-8 standar untuk database,
    serta merapikan spasi (trim) dan membuang baris kosong.
    """
    detected_enc, detected_del = detect_csv_format(data)
    eff_src_enc = detected_enc if (not src_encoding or src_encoding == "auto") else src_encoding
    eff_src_del = detected_del if (not src_delimiter or src_delimiter == "auto") else src_delimiter

    del_map = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}
    eff_target_del = del_map.get(target_delimiter, target_delimiter)
    if not eff_target_del:
        eff_target_del = ","

    quote_map = {
        "minimal": csv.QUOTE_MINIMAL,
        "all": csv.QUOTE_ALL,
        "nonnumeric": csv.QUOTE_NONNUMERIC,
    }
    eff_quoting = quote_map.get(quoting, csv.QUOTE_MINIMAL)

    try:
        text = data.decode(eff_src_enc, errors="replace")
    except Exception:
        text = data.decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(text), delimiter=eff_src_del)

    out_io = io.StringIO()
    writer = csv.writer(
        out_io,
        delimiter=eff_target_del,
        quoting=eff_quoting,
        lineterminator=line_terminator or "\r\n",
    )

    preview = []
    total_rows = 0

    for row in reader:
        if trim_whitespace:
            cells = [c.strip() for c in row]
        else:
            cells = list(row)

        if remove_empty_rows and not any(cells):
            continue

        writer.writerow(cells)
        total_rows += 1
        if len(preview) < 10:
            preview.append(cells[:15])

    eff_target_enc = target_encoding or "utf-8-sig"
    csv_bytes = out_io.getvalue().encode(eff_target_enc, errors="replace")

    return (
        csv_bytes,
        "reformatted_data.csv",
        f"text/csv; charset={eff_target_enc}",
        {
            "src_delimiter": eff_src_del,
            "target_delimiter": eff_target_del,
            "src_encoding": eff_src_enc,
            "target_encoding": eff_target_enc,
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


# ==============================================================================
# 5. GAMBAR KE PDF (IMAGES TO PDF)
# ==============================================================================

def images_to_pdf(
    images: List[Tuple[str, bytes]],
    page_size: str = "A4",
    orientation: str = "auto",
    margin_mm: int = 10,
    output_name: str = "dokumen_gambar.pdf",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Menggabungkan beberapa berkas gambar (JPG, PNG, WebP, dll.) menjadi 1 dokumen PDF."""
    if not images:
        raise ValueError("Tidak ada gambar yang dipilih untuk dikonversi.")

    doc = fitz.open()
    A4_W, A4_H = 595.28, 841.89
    margin_pts = max(0.0, float(margin_mm)) * 2.83465
    details = []

    for name, img_bytes in images:
        try:
            pil_img = Image.open(io.BytesIO(img_bytes))
        except Exception as e:
            doc.close()
            raise ValueError(f"Berkas '{name}' bukan gambar yang valid: {e}")

        # Pastikan mode RGB (hindari RGBA/P yang bermasalah pada JPEG stream)
        if pil_img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", pil_img.size, (255, 255, 255))
            if pil_img.mode == "RGBA":
                bg.paste(pil_img, mask=pil_img.split()[3])
            else:
                bg.paste(pil_img.convert("RGBA"), mask=pil_img.convert("RGBA").split()[3])
            pil_img = bg
        elif pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

        w_px, h_px = pil_img.size

        # Hitung dimensi halaman PDF
        if page_size.upper() == "A4":
            if orientation == "landscape" or (orientation == "auto" and w_px > h_px):
                pw, ph = A4_H, A4_W
            else:
                pw, ph = A4_W, A4_H
            rect = fitz.Rect(margin_pts, margin_pts, pw - margin_pts, ph - margin_pts)
        else:
            # Fit: gunakan ukuran gambar asli dalam points
            pw, ph = float(w_px), float(h_px)
            rect = fitz.Rect(0, 0, pw, ph)

        # Simpan ke JPEG stream berkualitas tinggi
        jpg_buf = io.BytesIO()
        pil_img.save(jpg_buf, format="JPEG", quality=92, optimize=True)

        page = doc.new_page(width=pw, height=ph)
        page.insert_image(rect, stream=jpg_buf.getvalue(), keep_proportion=True)
        details.append({"filename": name, "size_px": f"{w_px}x{h_px}"})

    out_bytes = doc.tobytes(deflate=True, garbage=4, clean=True)
    doc.close()

    safe_out = output_name.strip() or "dokumen_gambar.pdf"
    if not safe_out.lower().endswith(".pdf"):
        safe_out += ".pdf"

    return (
        out_bytes,
        safe_out,
        "application/pdf",
        {
            "total_images": len(images),
            "total_pages": len(images),
            "page_size": page_size,
            "images": details,
        },
    )


# ==============================================================================
# 6. PDF KE GAMBAR (PDF TO IMAGES)
# ==============================================================================

def pdf_to_images(
    data: bytes,
    dpi: int = 150,
    img_format: str = "jpeg",
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Mengekstrak seluruh halaman PDF menjadi berkas gambar (PNG / JPG)."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    ext = "jpg" if img_format.lower() in ("jpg", "jpeg") else "png"
    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"
    eff_dpi = max(72, min(int(dpi or 150), 300))

    # Jika hanya 1 halaman, unduh langsung gambarnya tanpa ZIP
    if total_pages == 1:
        pix = doc[0].get_pixmap(dpi=eff_dpi)
        img_bytes = pix.tobytes("jpeg" if ext == "jpg" else "png")
        doc.close()
        fname = f"{safe_base}_hal_1.{ext}"
        mime = "image/jpeg" if ext == "jpg" else "image/png"
        return (
            img_bytes,
            fname,
            mime,
            {"total_pages": 1, "format": ext, "dpi": eff_dpi, "is_zip": False},
        )

    # Multi-halaman: kemas ke dalam ZIP
    zip_buf = io.BytesIO()
    pad = max(len(str(total_pages)), 2)
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx in range(total_pages):
            pix = doc[idx].get_pixmap(dpi=eff_dpi)
            b = pix.tobytes("jpeg" if ext == "jpg" else "png")
            zf.writestr(f"{safe_base}_hal_{idx+1:0{pad}d}.{ext}", b)

    doc.close()
    return (
        zip_buf.getvalue(),
        f"{safe_base}_gambar_{ext}.zip",
        "application/zip",
        {"total_pages": total_pages, "format": ext, "dpi": eff_dpi, "is_zip": True},
    )


# ==============================================================================
# 7. KOMPRES PDF (COMPRESS PDF)
# ==============================================================================

def compress_pdf(
    data: bytes,
    level: str = "medium",
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Mengecilkan ukuran berkas dokumen PDF dengan mengoptimalkan stream & gambar."""
    orig_size = len(data)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    eff_level = level.lower() if level in ("low", "medium", "high") else "medium"

    # Jika level medium atau high: kompresi ulang gambar di dalam PDF
    if eff_level in ("medium", "high"):
        quality = 65 if eff_level == "high" else 78
        max_dim = 1400 if eff_level == "high" else 1800
        for page in doc:
            for img_info in page.get_images():
                xref = img_info[0]
                try:
                    base_img = doc.extract_image(xref)
                    if base_img:
                        raw_bytes = base_img["image"]
                        # Kompres jika ukuran gambar lebih dari 50 KB
                        if len(raw_bytes) > 50 * 1024:
                            im = Image.open(io.BytesIO(raw_bytes))
                            if im.mode in ("RGBA", "P", "LA"):
                                im = im.convert("RGB")
                            elif im.mode != "RGB":
                                im = im.convert("RGB")
                            if max(im.size) > max_dim:
                                im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                            out_io = io.BytesIO()
                            im.save(out_io, format="JPEG", quality=quality, optimize=True)
                            comp_raw = out_io.getvalue()
                            if len(comp_raw) < len(raw_bytes):
                                doc.update_stream(xref, comp_raw)
                except Exception:
                    pass

    comp_bytes = doc.tobytes(
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        clean=True,
    )
    doc.close()

    comp_size = len(comp_bytes)
    # Jika hasil kompresi ternyata lebih besar dari aslinya, gunakan aslinya
    if comp_size >= orig_size:
        comp_bytes = data
        comp_size = orig_size

    saved_bytes = max(0, orig_size - comp_size)
    saved_percent = round((saved_bytes / orig_size) * 100, 1) if orig_size > 0 else 0

    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"

    return (
        comp_bytes,
        f"{safe_base}_kompres.pdf",
        "application/pdf",
        {
            "original_size": orig_size,
            "compressed_size": comp_size,
            "saved_bytes": saved_bytes,
            "saved_percent": saved_percent,
            "total_pages": total_pages,
            "level": eff_level,
        },
    )


# ==============================================================================
# 7B. GENERATE PDF THUMBNAILS (VISUAL PREVIEWS)
# ==============================================================================

def generate_pdf_thumbnails(
    data: bytes,
    max_pages: int = 60,
    dpi: int = 60,
) -> Dict[str, Any]:
    """Menghasilkan thumbnail ringan (base64 JPEG) untuk pratinjau halaman PDF interaktif."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    eff_limit = min(total_pages, max(1, int(max_pages or 60)))
    thumbnails = []

    for idx in range(eff_limit):
        page = doc[idx]
        pix = page.get_pixmap(dpi=dpi)
        jpg_bytes = pix.tobytes("jpeg", jpg_quality=75)
        b64 = "data:image/jpeg;base64," + base64.b64encode(jpg_bytes).decode("ascii")
        thumbnails.append({
            "page": idx + 1,
            "width": pix.width,
            "height": pix.height,
            "thumb": b64,
        })

    doc.close()
    return {
        "total_pages": total_pages,
        "rendered_pages": eff_limit,
        "has_more": total_pages > eff_limit,
        "thumbnails": thumbnails,
    }


# ==============================================================================
# 8. PUTAR HALAMAN PDF (ROTATE PDF)
# ==============================================================================

def rotate_pdf(
    data: bytes,
    rotation: int = 90,
    pages_mode: str = "all",
    range_str: str = "",
    rotations_map: Optional[Dict[int, int]] = None,
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Memutar orientasi halaman berkas PDF secara lossless (90, 180, atau 270 derajat)."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    # Jika diberikan rotations_map individual (mis. {1: 90, 3: 180})
    if rotations_map and isinstance(rotations_map, dict):
        rotated_pages = []
        for p_str, r_deg in rotations_map.items():
            try:
                p_num = int(p_str)
                r_val = int(r_deg) % 360
            except (ValueError, TypeError):
                continue
            if 1 <= p_num <= total_pages and r_val != 0:
                page = doc[p_num - 1]
                page.set_rotation((page.rotation + r_val) % 360)
                rotated_pages.append(p_num)
        eff_rot = "custom"
        target_pages = rotated_pages
    else:
        # Validasi rotasi seragam
        eff_rot = int(rotation or 90) % 360
        if eff_rot not in (90, 180, 270):
            eff_rot = 90

        if pages_mode == "range":
            target_pages = parse_page_ranges(range_str, total_pages)
        else:
            target_pages = list(range(1, total_pages + 1))

        for p_num in target_pages:
            page = doc[p_num - 1]
            page.set_rotation((page.rotation + eff_rot) % 360)

    out_bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()

    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"
    suffix = f"putar_{eff_rot}deg" if isinstance(eff_rot, int) else "putar_kustom"
    return (
        out_bytes,
        f"{safe_base}_{suffix}.pdf",
        "application/pdf",
        {
            "total_pages": total_pages,
            "rotated_pages": target_pages,
            "rotation_applied": eff_rot,
        },
    )


# ==============================================================================
# 9. WATERMARK / CAP PDF (STAMP PDF)
# ==============================================================================

def watermark_pdf(
    data: bytes,
    text: str = "SALINAN",
    opacity: float = 0.25,
    font_size: int = 44,
    color: str = "gray",
    rotation_deg: int = 45,
    pages_mode: str = "all",
    range_str: str = "",
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Menyematkan stempel / watermark teks diagonal atau horizontal pada dokumen PDF."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    clean_text = (text or "SALINAN").strip().upper()
    if not clean_text:
        clean_text = "SALINAN"

    # Pemetaan warna
    color_map = {
        "gray": (0.5, 0.5, 0.5),
        "red": (0.85, 0.15, 0.15),
        "blue": (0.1, 0.35, 0.8),
        "dark": (0.2, 0.2, 0.2),
    }
    col = color_map.get(str(color).lower(), (0.5, 0.5, 0.5))

    eff_opacity = max(0.05, min(float(opacity or 0.25), 0.9))
    eff_size = max(18, min(int(font_size or 44), 96))
    eff_rot = int(rotation_deg if rotation_deg is not None else 45)

    if pages_mode == "range":
        target_pages = parse_page_ranges(range_str, total_pages)
    else:
        target_pages = list(range(1, total_pages + 1))

    for p_num in target_pages:
        page = doc[p_num - 1]
        rect = page.rect
        center = fitz.Point(rect.width / 2, rect.height / 2)
        fontname = "helv"
        tlen = fitz.get_text_length(clean_text, fontname=fontname, fontsize=eff_size)
        start_pt = fitz.Point(center.x - tlen / 2, center.y + eff_size / 3)

        if eff_rot != 0:
            morph = (center, fitz.Matrix(eff_rot))
            page.insert_text(
                start_pt,
                clean_text,
                fontname=fontname,
                fontsize=eff_size,
                color=col,
                morph=morph,
                fill_opacity=eff_opacity,
            )
        else:
            page.insert_text(
                start_pt,
                clean_text,
                fontname=fontname,
                fontsize=eff_size,
                color=col,
                fill_opacity=eff_opacity,
            )

    out_bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()

    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"
    return (
        out_bytes,
        f"{safe_base}_cap.pdf",
        "application/pdf",
        {
            "total_pages": total_pages,
            "stamped_pages": target_pages,
            "text": clean_text,
            "color": color,
            "opacity": eff_opacity,
            "rotation_deg": eff_rot,
        },
    )


# ==============================================================================
# 10. HAPUS HALAMAN PDF (DELETE PAGES)
# ==============================================================================

def delete_pages_pdf(
    data: bytes,
    pages_to_delete: str = "",
    base_filename: str = "dokumen",
) -> Tuple[bytes, str, str, Dict[str, Any]]:
    """Menghapus halaman tertentu dari berkas PDF."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Gagal membuka berkas PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("Berkas PDF dilindungi kata sandi. Harap buka kunci terlebih dahulu.")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("Dokumen PDF tidak memiliki halaman.")

    if not str(pages_to_delete or "").strip():
        doc.close()
        raise ValueError("Ketik nomor halaman yang ingin dihapus (misal: 1 atau 2, 4-5).")

    del_pages = set(parse_page_ranges(pages_to_delete, total_pages))
    if len(del_pages) >= total_pages:
        doc.close()
        raise ValueError("Tidak dapat menghapus seluruh halaman dokumen. Sisakan minimal 1 halaman.")

    # Simpan halaman yang tidak dihapus (0-indexed)
    keep_pages = [p - 1 for p in range(1, total_pages + 1) if p not in del_pages]
    doc.select(keep_pages)

    out_bytes = doc.tobytes(garbage=3, deflate=True)
    new_page_count = len(doc)
    doc.close()

    safe_base = re.sub(r'[\\/*?:"<>|]', "_", base_filename.replace(".pdf", "")).strip() or "dokumen"
    return (
        out_bytes,
        f"{safe_base}_hapus_hal.pdf",
        "application/pdf",
        {
            "original_pages": total_pages,
            "deleted_pages": sorted(list(del_pages)),
            "remaining_pages": new_page_count,
        },
    )

