# Arsitektur camerad

## Tujuan
Menata basis kode dari kumpulan berkas datar (90+ modul di root) menjadi paket Python per-domain, tanpa mengubah perilaku runtime. Aplikasi tetap dijalankan satu proses via `web_app.py`.

## Lapisan
1. **Entry point (root):** `web_app.py` dan skrip fase (`phase*`, `ingest.py`, `docstudio.py`, `llm_fix_final_combined.py`). Dieksekusi langsung.
2. **Domain (paket):** `avaya/`, `awe/`, `chat/`, `common/`, `db/`, `df_webhook/`, `evaluation/`, `handoff/`, `knowledge/`, `peraturan/`, `pipeline/`, `pustaka/`, `rag/`, `sop/`, `sosmed/`. Logika bisnis, kohesif per-domain.
3. **Routes (menu/HTTP):** `routes/` + modul `*_routes.py` di tiap domain. Ini sumbu "menu".
4. **Presentasi:** `templates/` (mis. `index.html`) + `static/`.
5. **Data & konfigurasi:** SQLite (`*.db`, gitignored), `.env` (gitignored), `requirements.txt`, Docker.

## Prinsip desain
- **Kohesi tinggi, kopling rendah:** kelompokkan berdasarkan domain, bukan tampilan.
- **Satu sumbu per lapisan:** domain (logika) tidak dicampur dengan menu (routes) atau tampilan (templates).
- **Patch runtime eksplisit:** patch RAG/step diimpor eksplisit di `web_app.py` agar urutan boot deterministik.

## Kenapa bukan per-menu (seperti index.html)?
Menu bersifat many-to-many terhadap modul backend: satu modul (mis. `rag.engine`) dipakai banyak menu, dan satu menu memakai banyak modul. Mengelompokkan per-menu akan menduplikasi kode & membuat ketergantungan silang. Sumbu menu tetap terwakili di `routes/` dan `templates/`.

## Riwayat migrasi
- **PR-5..PR-19:** pemindahan bertahap tiap domain ke paket, memakai shim kompatibilitas mundur (`sys.modules[__name__]`) di root agar impor lama tetap jalan di tiap langkah.
- **PR-20:** audit shim mati (0 ditemukan - semua masih dipakai).
- **PR-21/PR-22:** codemod `migrate_flatten` menulis-ulang seluruh impor nama-datar -> jalur paket, lalu menghapus 93 shim. Gerbang: `py_compile` (interpreter-agnostic) + verifikasi target + pemindaian residu; rollback otomatis. Verifikasi runtime via `python web_app.py` di venv Windows.
- **PR-23:** dokumentasi (`AGENTS.md`, dokumen ini) + guard CI/pre-commit + persiapan arsip skrip one-off.

## Menjaga kerapian
- Guard `scripts/oneoff/check_structure.py` menolak shim baru di root & memastikan semua `.py` compile.
- Skrip migrasi/one-off yang selesai diarsipkan ke `scripts/_archive/` (riwayat tetap ada via git) memakai `scripts/archive_oneoffs.sh`.
