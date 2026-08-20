# -*- coding: utf-8 -*-
"""Deteksi jenis file & ekstraksi teks untuk impor massal folder aturan/.

Port dari jakai (app/parsers/files.py) + adaptasi camerad. Menangani:
  * HTML peraturan   -> ditangani parser peraturan_parser (dideteksi di sini)
  * HTML lampiran    -> teks polos + nomor induk (termasuk berkas '..._lampiran'
                        TANPA ekstensi, sesuai penamaan unduhan TKB DJP)
  * PDF              -> teks (bila ada lapisan teks) atau perlu OCR (scan)
  * Gambar           -> OCR (jpg/png/tif/bmp/webp/gif)
  * ZIP/arsip & lain -> ditandai 'arsip'/'unknown' untuk perhatian manual

PyMuPDF (fitz) dipakai bila ada; jatuh ke pdftotext (poppler).

OCR (OPSIONAL) butuh PROGRAM tingkat sistem, bukan sekadar paket pip:
  * Tesseract-OCR + data bahasa 'ind'  (mesin OCR)
  * Poppler                            (dipakai pdf2image untuk render PDF)
`pip install -r requirements.txt` hanya memasang pembungkus Python
(pytesseract, pdf2image, pillow); TANPA kedua program di atas OCR tidak jalan
dan berkas scan tetap ditandai 'perlu_ocr'.

Bila program terpasang tapi tidak ada di PATH, tunjuk lokasinya lewat env:
  * PERATURAN_TESSERACT_CMD = path lengkap ke tesseract.exe
  * PERATURAN_POPPLER_PATH  = folder 'bin' Poppler (berisi pdftoppm)
  * PERATURAN_OCR_LANG      = bahasa OCR (default 'ind')

Bila biner tak ada, berkas scan/gambar hanya DITANDAI (perlu_ocr) tanpa
menggagalkan proses.

== Pemantauan OCR ==
Karena OCR bisa lama & tampak 'diam', fungsi OCR mencetak progres ke terminal
(stdout, flush) per berkas dan per halaman, mis:
    [peraturan][ocr] mulai OCR PDF: <berkas> (12 hal)
    [peraturan][ocr]   <berkas> hal 3/12
    [peraturan][ocr] selesai OCR PDF: <berkas> -> 5123 karakter
Selain itu ada hook callback opsional (set_progress_cb) yang dipakai
peraturan_batch untuk menampilkan progres yang sama di UI.

Catatan MuPDF: sebagian PDF punya content-stream dengan token tak sah (mis.
operator 'q'/'Q' tertulis dobel jadi 'qq'/'QQ'). MuPDF akan MELEWATI token itu
dan tetap mengekstrak teks, tetapi mencetak peringatan 'syntax error: unknown
keyword' ke stderr. Peringatan itu non-fatal dan hanya bikin konsol berisik,
jadi ditekan lewat fitz.TOOLS.mupdf_display_errors(False).
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

import peraturan.parser as tkb_djp

# Ambang minimal karakter "berarti" per halaman agar PDF dianggap ada teks.
MIN_CHAR_PER_HAL = 80

# Ekstensi gambar yang bisa di-OCR.
IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".gif")
# Arsip: perlu diekstrak manual dulu.
ARSIP_EXT = (".zip", ".rar", ".7z", ".tar", ".gz")
# Berkas sistem yang diabaikan.
SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


def _log(msg):
    """Cetak progres ke terminal (flush agar langsung terlihat saat berjalan)."""
    try:
        print("[peraturan][ocr] " + msg, flush=True)
    except Exception:
        pass


# Hook progres OPSIONAL: di-set oleh peraturan_batch supaya kemajuan OCR bisa
# ikut tampil di UI. Bentuk: cb(event: str, data: dict). Tidak memengaruhi hasil
# bila tidak diset.
_PROGRESS_CB = None


def set_progress_cb(cb):
    """Pasang/lepas callback progres OCR (None untuk melepas)."""
    global _PROGRESS_CB
    _PROGRESS_CB = cb


def _emit(event, **data):
    cb = _PROGRESS_CB
    if cb is None:
        return
    try:
        cb(event, data)
    except Exception:
        pass


def _n_nonspace(teks):
    return len(re.sub(r"\s+", "", teks or ""))


def _ocr_lang() -> str:
    return os.environ.get("PERATURAN_OCR_LANG") or "ind"


def _poppler_path():
    p = os.environ.get("PERATURAN_POPPLER_PATH")
    return p if p else None


def _tesseract_cmd():
    """Lokasi biner tesseract: dari env PERATURAN_TESSERACT_CMD bila ada &
    valid, jika tidak cari di PATH."""
    c = os.environ.get("PERATURAN_TESSERACT_CMD")
    if c and os.path.isfile(c):
        return c
    return shutil.which("tesseract")


@dataclass
class FileInfo:
    path: str
    kategori: str = ""
    tipe: str = "unknown"       # regulation_html | lampiran_html | pdf_text |
                                # pdf_scan | image_text | image_scan | arsip | unknown
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


def _is_lampiran_ekstensi_kosong(path: str) -> bool:
    """True untuk berkas lampiran HTML yang diunduh TANPA ekstensi, mis.
    'https___..._id=<hash>_lampiran'."""
    base = os.path.basename(path).lower()
    root, ext = os.path.splitext(base)
    return base.endswith("_lampiran") and ext == ""


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


_MUPDF_SENYAP = False


def _fitz():
    """Impor PyMuPDF sekali & bungkam peringatan MuPDF (non-fatal) ke stderr.

    Peringatan seperti "syntax error: unknown keyword: 'qq'/'QQ'" berasal dari
    content-stream PDF yang tidak sepenuhnya standar; MuPDF melewati token itu
    dan teks tetap terekstrak. Kita hanya menekan spam peringatannya.
    """
    global _MUPDF_SENYAP
    import fitz  # PyMuPDF
    if not _MUPDF_SENYAP:
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:
            pass
        _MUPDF_SENYAP = True
    return fitz


def _pdf_text_fitz(path: str):
    fitz = _fitz()
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
    return _tesseract_cmd() is not None


def _set_tess_cmd():
    """Arahkan pytesseract ke biner yang benar (env atau PATH). Kembalikan
    modul pytesseract, atau None bila tak tersedia."""
    cmd = _tesseract_cmd()
    if not cmd:
        return None
    try:
        import pytesseract
    except Exception:
        return None
    try:
        pytesseract.pytesseract.tesseract_cmd = cmd
    except Exception:
        pass
    return pytesseract


def ocr_pdf(path: str, lang: str = None, dpi: int = 300, max_hal: int = 30) -> str:
    """OCR PDF scan -> teks. Perlu biner tesseract + data bahasa 'ind' + poppler.

    Aman dipanggil tanpa tesseract/poppler: mengembalikan string kosong.
    Mencetak progres per halaman ke terminal + memancarkan event progres.
    """
    base = os.path.basename(path)
    pytesseract = _set_tess_cmd()
    if pytesseract is None:
        _log("OCR PDF dilewati (tesseract tak ditemukan): %s" % base)
        return ""
    try:
        from pdf2image import convert_from_path
    except Exception:
        _log("OCR PDF dilewati (pdf2image belum terpasang): %s" % base)
        return ""
    lang = lang or _ocr_lang()
    try:
        images = convert_from_path(path, dpi=dpi, poppler_path=_poppler_path())
    except Exception as e:
        _log("OCR PDF gagal render (Poppler?): %s (%s)" % (base, str(e)[:140]))
        return ""
    total = min(len(images), max_hal)
    _log("mulai OCR PDF: %s (%d hal)" % (base, total))
    _emit("ocr_pdf_start", path=path, pages=total)
    hasil = []
    for i, img in enumerate(images[:max_hal], start=1):
        try:
            hasil.append(pytesseract.image_to_string(img, lang=lang))
        except Exception:
            try:
                hasil.append(pytesseract.image_to_string(img))
            except Exception:
                pass
        _log("  %s hal %d/%d" % (base, i, total))
        _emit("ocr_page", path=path, i=i, n=total)
    teks = "\n".join(hasil)
    _log("selesai OCR PDF: %s -> %d karakter" % (base, _n_nonspace(teks)))
    _emit("ocr_pdf_done", path=path, chars=_n_nonspace(teks))
    return teks


def ocr_image(path: str, lang: str = None) -> str:
    """OCR satu berkas gambar -> teks. Perlu tesseract + data bahasa 'ind'."""
    base = os.path.basename(path)
    pytesseract = _set_tess_cmd()
    if pytesseract is None:
        _log("OCR gambar dilewati (tesseract tak ditemukan): %s" % base)
        return ""
    try:
        from PIL import Image
    except Exception:
        return ""
    lang = lang or _ocr_lang()
    try:
        img = Image.open(path)
    except Exception:
        return ""
    _log("mulai OCR gambar: %s" % base)
    _emit("ocr_image_start", path=path)
    try:
        teks = pytesseract.image_to_string(img, lang=lang)
    except Exception:
        try:
            teks = pytesseract.image_to_string(img)
        except Exception:
            teks = ""
    _log("selesai OCR gambar: %s -> %d karakter" % (base, _n_nonspace(teks)))
    _emit("ocr_image_done", path=path, chars=_n_nonspace(teks))
    return teks


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


def extract_image(path: str, do_ocr: bool = False):
    """Kembalikan (teks, perlu_ocr) untuk berkas gambar.

    Gambar tidak punya lapisan teks, jadi teks hanya diperoleh via OCR.
    """
    if do_ocr:
        teks = ocr_image(path)
        if len(re.sub(r"\s+", "", teks)) >= 20:
            return _clean(teks), False
    return "", True


def classify(path: str, do_ocr: bool = False) -> FileInfo:
    info = FileInfo(path=path)
    low = path.lower()
    base = os.path.basename(low)

    if base in SKIP_NAMES:
        info.tipe, info.catatan = "unknown", "berkas sistem (diabaikan)"
        return info

    # 1) HTML peraturan / lampiran (termasuk '..._lampiran' TANPA ekstensi)
    if is_html(low) or _is_lampiran_ekstensi_kosong(low):
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

    # 2) PDF
    if low.endswith(".pdf"):
        teks, n, perlu = extract_pdf(path, do_ocr=do_ocr)
        info.teks, info.n_halaman, info.perlu_ocr = teks, n, perlu
        info.nomor_teks = _cari_nomor(teks)
        info.tipe = "pdf_scan" if perlu else "pdf_text"
        if perlu and do_ocr and not has_tesseract():
            info.catatan = "PDF scan; OCR belum aktif (pasang Tesseract 'ind' + Poppler)"
        return info

    # 3) Gambar -> OCR
    if low.endswith(IMG_EXT):
        teks, perlu = extract_image(path, do_ocr=do_ocr)
        info.teks, info.perlu_ocr = teks, perlu
        info.nomor_teks = _cari_nomor(teks)
        info.tipe = "image_scan" if perlu else "image_text"
        if perlu and not has_tesseract():
            info.catatan = "gambar; OCR belum aktif (butuh tesseract 'ind')"
        return info

    # 4) Arsip -> ekstrak manual
    if low.endswith(ARSIP_EXT):
        info.tipe = "arsip"
        info.catatan = "arsip (zip/rar/7z) - ekstrak manual lalu impor ulang isinya"
        return info

    # 5) Format lain -> perhatian manual
    info.tipe = "unknown"
    info.catatan = "format belum didukung - perlu perhatian manual"
    return info
