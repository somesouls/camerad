# -*- coding: utf-8 -*-
"""Deteksi jenis file & ekstraksi teks untuk impor massal folder aturan/.

Port dari jakai (app/parsers/files.py). Menangani:
  * HTML peraturan  -> ditangani parser peraturan_parser (dideteksi di sini)
  * HTML lampiran   -> teks polos + nomor induk
  * PDF             -> teks (bila ada lapisan teks) atau perlu OCR (scan)

PyMuPDF (fitz) dipakai bila ada; jatuh ke pdftotext (poppler). OCR (tesseract)
OPSIONAL: bila biner tesseract tak ada, file scan hanya DITANDAI 'perlu_ocr'.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

import peraturan_parser as tkb_djp

# Ambang minimal karakter "berarti" per halaman agar PDF dianggap ada teks.
MIN_CHAR_PER_HAL = 80


@dataclass
class FileInfo:
    path: str
    kategori: str = ""
    tipe: str = "unknown"       # regulation_html | lampiran_html | pdf_text | pdf_scan | unknown
    teks: str = ""
    nomor_teks: str = ""
    n_halaman: int = 0
    perlu_ocr: bool = False
    catatan: str = ""


def _clean(t: str) -> str:
    if not t:
        return ""
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


RE_NOMOR = re.compile(
    r"(PER|PMK|KEP|SE)\s*-?\s*\d+[/A-Z0-9.\-]*(?:/\s*(?:PJ|PMK)[^\s]*)?/?\s*\d{4}"
    r"|NOMOR\s+\d+\s+TAHUN\s+\d{4}",
    re.I,
)


def _cari_nomor(teks: str) -> str:
    m = RE_NOMOR.search(teks or "")
    return _clean(m.group(0)) if m else ""


def is_html(path: str) -> bool:
    return path.lower().endswith((".html", ".htm"))


def looks_like_lampiran(path: str, html: str = "") -> bool:
    name = os.path.basename(path).lower()
    if "lampiran" in name:
        return True
    if html and BeautifulSoup is not None:
        soup = BeautifulSoup(html, "lxml")
        if soup.select_one(".isi-aturan") or soup.select_one("#peraturan"):
            return False
        head = _clean(soup.get_text(" "))[:400].upper()
        if head.startswith("LAMPIRAN") or "\nLAMPIRAN" in head:
            return True
    return False


def extract_lampiran_html(html: str):
    """Kembalikan (teks_polos, nomor_induk) dari HTML lampiran."""
    if BeautifulSoup is None:
        return _clean(re.sub(r"<[^>]+>", " ", html)), ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup((["script", "style"])):
        tag.decompose()
    teks = _clean(soup.get_text(" "))
    return teks, _cari_nomor(teks)


def _pdf_text_fitz(path: str):
    import fitz  # PyMuPDF
    doc = fitz.open(path)
    parts = [pg.get_text("text") for pg in doc]
    n = doc.page_count
    doc.close()
    return "\n".join(parts), n


def _pdf_text_poppler(path: str):
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, text=True, timeout=120,
        )
        txt = out.stdout or ""
    except Exception:
        txt = ""
    n = txt.count("\f") + 1 if txt else 0
    return txt, n


def has_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def ocr_pdf(path: str, lang: str = "ind", dpi: int = 300, max_hal: int = 30) -> str:
    """OCR PDF scan -> teks. Perlu biner tesseract + data bahasa 'ind'.

    Aman dipanggil tanpa tesseract: mengembalikan string kosong.
    """
    if not has_tesseract():
        return ""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except Exception:
        return ""
    try:
        images = convert_from_path(path, dpi=dpi)
    except Exception:
        return ""
    hasil = []
    for img in images[:max_hal]:
        try:
            hasil.append(pytesseract.image_to_string(img, lang=lang))
        except Exception:
            try:
                hasil.append(pytesseract.image_to_string(img))
            except Exception:
                pass
    return "\n".join(hasil)


def extract_pdf(path: str, do_ocr: bool = False):
    """Kembalikan (teks, n_halaman, perlu_ocr)."""
    teks, n = "", 0
    try:
        teks, n = _pdf_text_fitz(path)
    except Exception:
        teks, n = _pdf_text_poppler(path)
    if n <= 0:
        n = 1
    padat = len(re.sub(r"\s+", "", teks))
    cukup = padat >= MIN_CHAR_PER_HAL * max(n, 1)
    if cukup:
        return _clean(teks), n, False
    if do_ocr:
        ocr = ocr_pdf(path)
        if len(re.sub(r"\s+", "", ocr)) > padat:
            return _clean(ocr), n, False
    return _clean(teks), n, True


def classify(path: str, do_ocr: bool = False) -> FileInfo:
    info = FileInfo(path=path)
    low = path.lower()
    if is_html(low):
        try:
            html = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:  # pragma: no cover
            info.tipe, info.catatan = "unknown", f"gagal baca: {e}"
            return info
        if not looks_like_lampiran(path, html) and tkb_djp.is_regulation_html(html):
            info.tipe = "regulation_html"
        else:
            info.tipe = "lampiran_html"
            info.teks, info.nomor_teks = extract_lampiran_html(html)
        return info
    if low.endswith(".pdf"):
        teks, n, perlu = extract_pdf(path, do_ocr=do_ocr)
        info.teks, info.n_halaman, info.perlu_ocr = teks, n, perlu
        info.nomor_teks = _cari_nomor(teks)
        info.tipe = "pdf_scan" if perlu else "pdf_text"
        return info
    info.catatan = "ekstensi tidak didukung"
    return info
