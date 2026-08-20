# Perubahan lanjutan — Sidebar rail, disetujui_oleh, statistik pemakaian pustaka

## A. Sidebar mode "rail" (mini) — bukan sembunyi total
- Saat tidak dipin, sidebar **tidak hilang** melainkan menyempit jadi rail ~78px
  yang menampilkan **logo + ikon menu saja** (tanpa tulisan).
- **Hover** rail -> melebar jadi 280px (overlay, konten tidak bergeser) menampilkan
  semua label, tombol "Chat Baru", dan Riwayat Percakapan.
- Tombol **pin (panah)** di kanan-atas: saat dipin sidebar tetap 280px dan konten
  bergeser; status disimpan di localStorage.
- Mobile (<=820px): tetap drawer off-canvas dengan tombol hamburger (rail dimatikan).
- Implementasi: CSS `:hover`/`body.sidebar-pinned` (tanpa elemen edge-trigger lama),
  ada di base.html + skrip fragmen sidebar.

## B. Pencatatan `disetujui_oleh` pada persetujuan katalog
- `intentmap_db.py`: kolom baru `disetujui_oleh`, `disetujui_pada` pada tabel
  `intentmap_catalog` (dengan migrasi ALTER TABLE otomatis untuk DB lama).
- `approve_description(conn, iid, edits=None, disetujui_oleh=None)` kini menyimpan
  nama penyetuju + waktu setuju.
- `web_app.py` `/api/intentmap/approve`: mengambil nama analis dari sesi login
  (`request.state.user`) dan meneruskannya ke `approve_description`.
- `templates/intentmap.html`: baris katalog terverifikasi kini menampilkan
  "oleh <nama>" di bawah badge status.

## C. Statistik pemakaian pustaka
- Modul baru `pustaka_stats.py` (tabel `pustaka_pemakaian` di DB analitik):
  mencatat berapa kali tiap entri pustaka benar-benar dipakai untuk konteks.
- `knowledge_ctx.py`: setiap blok (Glosarium, Disambiguasi, Peta Intent, Katalog)
  mencatat entri hasil `match()` via `_log_pustaka` (aman-gagal, tak mengganggu chat).
- `web_app.py`: endpoint baru `GET /api/pustaka/stats` (ringkasan + top entri per pustaka).
- **Tampilan:** kartu **"Statistik Pemakaian Pustaka"** di halaman **Dashboard**
  (4 kolom: Glosarium, Disambiguasi, Peta Intent, Katalog) — total pemakaian +
  daftar entri paling sering dipakai. Mudah dipindah bila ingin ditaruh di tempat lain.

## Build & validasi
- Dibangun ulang deterministik lewat `/data/build_all.py` dari zip asli.
- `py_compile` OK (web_app, intentmap_db, knowledge_ctx, pustaka_stats).
- 9 template ter-render lewat Jinja (`validate.py`) tanpa error.
- Catatan: butuh migrasi otomatis saat pertama dijalankan (kolom baru + tabel baru
  dibuat on-the-fly). FastAPI belum terpasang di sandbox, jadi endpoint diuji secara
  statis (kompilasi + render), bukan runtime.
