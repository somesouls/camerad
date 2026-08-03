# Camerad Studio — v11 (Perbaikan ekstraksi PDF + OCR di Studio Dokumen)

## 1. Perbaikan bug: PDF teks-terpilih terbaca “nyaris tanpa teks”
**Penyebab:** `parse_pdf` lama memakai **pypdf** sebagai ekstraktor utama dan hanya jatuh ke **PyMuPDF (fitz)** kalau hasil pypdf < 20 karakter. Untuk sebagian PDF (font/enkoding tertentu) pypdf mengembalikan teks kosong sehingga muncul catatan “nyaris tanpa teks”, padahal teksnya sebenarnya bisa dipilih.

**Perbaikan:** sekarang PDF diekstrak dengan **kedua** mesin sekaligus (`pypdf` + `PyMuPDF`), lalu diambil hasil yang **lebih lengkap** (`_pdf_text_layer`). PDF teks-terpilih Anda kini terbaca tanpa perlu OCR. `pg.get_text("text")` dipakai eksplisit pada jalur fitz.

## 2. OCR (dokumen hasil pindai & berkas gambar)
Ditambahkan OCR opsional berbasis **Tesseract** + **PyMuPDF** (render halaman ke gambar) + **pytesseract/Pillow**:
- **PDF pindai (gambar):** bila lapisan teks nyaris kosong (ambang: < `max(40, halaman×8)` karakter) **dan** OCR tersedia, tiap halaman dirender (zoom 2×, maks 40 halaman) lalu di-OCR. Catatan info: “Teks diekstrak via OCR…”.
- **Berkas gambar** (PNG, JPG/JPEG, TIF/TIFF, BMP, WEBP, GIF): kini bisa diunggah langsung dan di-OCR.
- **Bahasa OCR:** otomatis pakai `ind+eng` bila paket bahasa tersedia; jika tidak, fallback ke default Tesseract.
- **Degradasi anggun:** jika Tesseract **belum** terpasang, PDF pindai memberi catatan cara mengaktifkan OCR, dan unggah berkas gambar ditolak dengan pesan jelas (bukan crash).

### Cara mengaktifkan OCR di server (mesin Anda)
Pustaka Python-nya sudah ada (`pytesseract`, `Pillow`, `PyMuPDF`). Yang perlu ditambah hanya **biner Tesseract**:

**Windows:** unduh installer “Tesseract at UB Mannheim”, install (mis. `C:\Program Files\Tesseract-OCR`). Saat install, centang **Additional language data → Indonesian** (`ind`). Pastikan folder Tesseract ada di PATH, atau set di kode: `pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"`.

**Ubuntu/Debian:** `sudo apt-get install -y tesseract-ocr tesseract-ocr-ind`

**macOS:** `brew install tesseract tesseract-lang`

Setelah Tesseract terpasang, **restart** `uvicorn`. Studio otomatis mendeteksi ketersediaannya (`docstudio.ocr_available()`), tanpa perubahan kode lain. Verifikasi cepat: jalankan `tesseract --version` di terminal.

> Catatan: di lingkungan build (sandbox) biner Tesseract tidak terpasang, jadi OCR diverifikasi lewat py_compile + jalur degradasi. OCR aktif nyata di mesin Anda begitu Tesseract dipasang.

## 3. Progres rencana (Notion) dicentang
Halaman **Camerad Studio — Rencana Pengembangan**: §10 (Epik A–E) dan §9 (Fase 1–Fase 5) ditandai selesai.

## Berkas yang berubah
- `docstudio.py` — `_pdf_text_layer`, `parse_pdf` (return 4-nilai + `ocr_used`), `ocr_available`, `ocr_pdf`, `ocr_image`, `_ocr_lang`, `_ocr_image_obj`; `IMAGE_EXTS` + ditambah ke `SUPPORTED_EXTS`; cabang `extract()` untuk PDF (catatan OCR) & gambar.
- `templates/studio.html` — `accept` menerima format gambar; teks format + deskripsi menyebut OCR.
- `templates/base.html` — penanda **Build v11 · Fase 1–5 + OCR**.

## Validasi v11
- `py_compile` docstudio.py / studio_routes.py / web_app.py: OK
- Parse Jinja 13 template + render studio.html: OK
- Smoke `docstudio`: `_pdf_text_layer` PDF teks-terpilih terbaca; `ocr_available()` = False di sandbox (degradasi benar); ekstraksi TXT/CSV/XLSX/DOCX tetap OK.
