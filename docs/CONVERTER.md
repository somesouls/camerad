# Converter: PDF / gambar ke teks

URL: `/converter` (menu Umum). Tidak mengubah Studio Dokumen atau mesin RAG.

## Pemakaian
1. Pilih beberapa PDF/gambar, drag-drop, atau Ctrl+V screenshot di luar kolom teks.
2. Klik **Ekstrak teks**. File diproses berurutan; OCR berjalan di background dengan polling.
3. Periksa/edit hasil, lalu **Salin teks** atau **Unduh .txt** per file. Salin semua/unduh gabungan juga tersedia.

Batas: 10 file di browser, 20 MB/file, PDF 1–30 halaman, gambar 20 MP, hasil 1 juta karakter. PDF bersandi dan gambar multiframe ditolak dengan pesan jelas. PDF dengan sedikit/tanpa teks memakai OCR; halaman digital mempertahankan lapisan teks. Gambar yang tertanam pada halaman PDF yang sudah memiliki teks tidak di-OCR ulang. Hasil tabel/kolom tidak menjamin tata letak asli.

## Deploy
Dependency Python sudah ada di requirements: PyMuPDF, Pillow, pytesseract, python-multipart.

Linux Debian/Ubuntu:
```sh
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng
pip install -r requirements.txt
```
Dockerfile memasang Tesseract dan bahasa Indonesia/Inggris secara otomatis pada image baru.
Windows: pasang Tesseract beserta `ind.traineddata` dan `eng.traineddata`. Tambahkan executable ke PATH atau set di `.env`:
```text
CONVERTER_TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```
Kemudian `git pull` dan restart proses web. Kode tidak dipasang otomatis ke server oleh asisten.
Jika peran sudah memakai daftar menu eksplisit, aktifkan **Converter** pada halaman `/akses`; konfigurasi lama tidak diperluas diam-diam.

## Privasi / batas operasional
- Semua ekstraksi lokal; tidak ada panggilan LLM, cloud OCR, ingest RAG, atau penulisan dokumen permanen.
- Multipart dapat memakai spool sementara OS selama upload, ditutup setelah dibaca. Hasil job hanya disimpan di memori proses, maksimum 40 hasil, 8 antrean aktif global, 2 aktif/pengguna. Maksimum 12 hasil sementara/pengguna.
- Owner job diverifikasi dari sesi dan izin menu pada seluruh API. ID acak bukan pengganti autentikasi.
- Hasil tersedia 30 menit dan dibersihkan saat akses berikutnya. Browser menghapus job server setelah hasil diambil. Muat ulang/restart menghapus hasil halaman; unduh dahulu.
- Saat ini server memakai satu proses web. Bila deploy kelak menggunakan beberapa worker/replika, gunakan sticky routing atau pindahkan penyimpanan job ke backend bersama sebelum mengaktifkan converter. Job tidak persisten.
- Timeout 20 detik/OCR halaman dan 240 detik/file diperiksa per halaman; antrean/polling menjaga request HTTP singkat. Limit body 20 MB + overhead ditegakkan sebelum parser menerima body lengkap. Tetapkan limit reverse proxy sedikit lebih besar (mis. 21 MB).
- Rollback fitur: set `CONVERTER_ENABLED=0` lalu restart; route lain tetap berjalan.

## Pengujian
```sh
python -m unittest tests.test_converter tests.test_converter_routes -v
node --check static/app/converter.js
python scripts/oneoff/check_structure.py
```
Workflow Converter tests juga menguji Tesseract asli (gambar teks sintetis), PDF digital/scan, batas upload, job ownership, dan izin menu.
