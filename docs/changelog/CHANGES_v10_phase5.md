# Camerad Studio — v10 (Fase 5 = Epik C: Studio Dokumen)

Pengganti NotebookLM: unggah dokumen → hasilkan **Ringkasan / Laporan / Mindmap / Tabel data** sebagai **dokumen untuk diunduh/preview** (bukan tulis balik ke DB). Isi jawaban **hanya dari dokumen terunggah + DB global** (konsisten Epik B); LLM cloud hanya merangkai kalimat, tanpa web/pengetahuan eksternal.

## Menu baru
- Sidebar: **Studio Dokumen** (`/studio`), setelah Analisis Dialogflow. Label build diperbarui ke **Build v10**.

## Format unggahan yang didukung
PDF (`pypdf`, cadangan PyMuPDF/`fitz`), Word `.docx` (`python-docx`), PowerPoint `.pptx` (`python-pptx`), Excel `.xlsx` (`openpyxl`), CSV (`csv` bawaan, auto-deteksi pemisah), Teks `.txt/.md`. Maks 25 MB. Semua pustaka parser sudah terpasang di lingkungan (diverifikasi).

## Keluaran
1. **Ringkasan** — ikhtisar + poin kunci (Markdown, unduh `.md`).
2. **Laporan** — dokumen terstruktur (Ringkasan Eksekutif / Temuan / Analisis / Kesimpulan), unduh `.md`.
3. **Mindmap** — diagram **Mermaid** (dirender via CDN; fallback otomatis ke kode + outline bila offline). Unduh `.mmd` dan outline `.md`.
4. **Tabel data** — jika dokumen sudah punya tabel (xlsx/csv/docx) dipakai langsung; jika tidak, diekstrak oleh AI dari teks. Preview HTML + unduh **XLSX** & **CSV**.

## Penanganan dokumen besar
`docstudio.chunk_text` memecah teks per-paragraf (≤ 6000 char/potongan). Dokumen besar diproses **map-reduce**: tiap potongan diekstrak faktanya (tahap map, maks 14 potongan), lalu digabung menjadi keluaran akhir (tahap reduce). Dokumen kecil langsung satu tahap.

## Guardrail (konsisten Epik B)
System prompt `docstudio.GUARDRAIL`: jawab HANYA dari konteks dokumen (+ konteks internal untuk konsistensi istilah); dilarang memakai pengetahuan umum/web; bila info tak ada, akui jujur. Konteks internal diambil dari `knowledge_ctx.build_analysis_context`.

## Berkas & arsitektur
- **`docstudio.py`** (baru, mandiri — tanpa FastAPI/LLM): parser semua format, chunking, prompt builder (map/reduce/single), konverter keluaran (outline→Mermaid, JSON→tabel, XLSX/CSV/HTML). Mudah diuji terpisah.
- **`studio_routes.py`** (baru): `register(app, ...)` mendaftarkan route; menyimpan dokumen di `_studio/<docid>/` (text.txt, tables.json, meta.json, artefak keluaran).
- **`web_app.py`**: memanggil `studio_routes.register(...)` sebelum blok `__main__`.
- **`templates/studio.html`** (baru): drag-drop upload, info dokumen + cuplikan, pemilih 4 keluaran, input fokus opsional, area hasil (Markdown/Mermaid/tabel) + tombol unduh.
- **`templates/base.html`**: link sidebar Studio; kartu Tanya AI generik dikecualikan di halaman Studio (`active_page not in ['dashboard','studio','']`).

## Route
- `GET /studio` — halaman.
- `POST /api/studio/upload` (multipart `file`) → `{ok, docid, filename, ext, n_chars, pages, tables, preview, note}`.
- `POST /api/studio/generate` (JSON `{docid, output, question?}`) → keluaran + `downloads[]`.
- `GET /api/studio/download?docid=&f=&name=` → unduh artefak (xlsx/csv/md/mmd).

## Validasi v10
- `py_compile` web_app.py / studio_routes.py / docstudio.py: OK
- Parse Jinja 13 template: OK; render `studio.html`: drop-zone + pemilih keluaran ada, kartu Tanya AI generik absen; halaman lain: link Studio + kartu Tanya AI ada.
- `node --check` JS `studio.html`: OK (tanpa Jinja di script).
- Smoke parser `docstudio`: TXT/CSV/XLSX/DOCX/PPTX/PDF, outline→Mermaid, JSON→tabel, XLSX out, chunking — semua OK.

## Catatan runtime
FastAPI tidak terpasang di sandbox, jadi route diverifikasi via py_compile + parser smoke. Verifikasi akhir di server: `uvicorn web_app:app ...`, buka **Studio Dokumen**, unggah contoh (mis. PDF/Docx), pilih keluaran. Mindmap butuh internet untuk render Mermaid (CDN); bila offline, tampil sebagai kode Mermaid + outline (tetap bisa diunduh).
