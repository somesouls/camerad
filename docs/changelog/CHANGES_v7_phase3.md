# Camerad Studio — v7 (Fase 3: Epik E — Manajemen Siklus-Hidup Intent)

Fase 3 mengeksekusi **Epik E** dari rencana pengembangan: membedakan intent yang benar-benar terpakai vs tidak, mengelola (soft delete), menyimpan tanggal terakhir dipanggil, serta menerapkan kebijakan retensi 6 bulan.

## Konsep & sumber data
- **Sumber “terpanggil” = tabel `interactions`** (data Dialogflow) yang berada di database yang sama dengan **Katalog Intent** (`intentmap_catalog`). Sebuah intent dianggap terpanggil bila namanya (`intent`) muncul di `interactions.intent_name`.
- **`last_called_at`** diturunkan dari `MAX(day)` per intent (tanggal UTC), **`frekuensi_panggil`** dari `COUNT(*)`.
- **Kandidat retensi** = intent yang **belum pernah dipanggil** ATAU **idle ≥ N bulan** (default 6, dapat diatur), dan **belum** ditandai soft-deleted.

---

## Backend — `intentmap_db.py`

### Kolom baru pada `intentmap_catalog` (migrasi otomatis di `init_catalog`)
- `soft_deleted` (INTEGER, default 0)
- `soft_deleted_at` (TEXT)
- `soft_deleted_by` (TEXT) — pencatatan siapa yang menandai
- `last_called_at` (TEXT, `YYYY-MM-DD`)
- indeks `idx_cat_softdel`

Migrasi bersifat aditif & idempoten (pola `ALTER TABLE ... ADD COLUMN` seperti kolom `disetujui_oleh`/`lang` sebelumnya) — **aman untuk database lama**, tidak menyentuh data/deskripsi yang sudah ada.

### Fungsi baru
- `refresh_lifecycle(conn)` — perbarui `last_called_at` & `frekuensi_panggil` seluruh katalog dari `interactions` (satu `GROUP BY`). Aman bila tabel `interactions` belum ada.
- `lifecycle_overview(conn, retensi_bulan=6)` — hitung: total, terpanggil, tidak terpanggil, soft-deleted, kandidat retensi.
- `lifecycle_list(conn, filt, q, limit, retensi_bulan, lang)` — daftar intent + status siklus-hidup (`aktif` / `retensi` / `tidak_dipanggil` / `soft_deleted`), idle (hari & bulan), diurut “belum pernah dipanggil dulu, lalu paling lama idle”. Filter: `all|dipanggil|tidak|retensi|softdeleted|aktif`.
- `set_soft_delete(conn, ident, deleted, user)` — tandai/pulihkan soft-delete; `ident` bisa **id katalog atau nama intent**.
- `_cat_row(...)` kini turut mengembalikan field siklus-hidup, sehingga **status juga tersedia di Katalog Intent** yang sudah ada.

---

## Backend — `web_app.py` (route & API baru)
- `GET  /lifecycle` — halaman baru (sidebar aktif = `lifecycle`).
- `GET  /api/lifecycle/summary` — menyegarkan `last_called_at` dari `interactions` lalu mengembalikan ringkasan (param: `months`, `refresh`).
- `GET  /api/lifecycle/list` — daftar intent + status (param: `filter`, `q`, `lang`, `limit`, `months`).
- `POST /api/lifecycle/softdelete/save` — tandai/pulihkan soft-delete. Berakhiran `/save` sehingga **otomatis butuh peran dengan hak edit** (admin/analis) dan mencatat `soft_deleted_by` dari sesi login.

## Frontend
- `templates/base.html` — menu sidebar baru **“Siklus Hidup Intent”** (ikon jam, `active_page=='lifecycle'`), diletakkan tepat setelah **Peta Intent**.
- `templates/lifecycle.html` — halaman baru:
  - Toolbar: **retensi (bulan)** dapat diatur, filter **bahasa**, pencarian, tombol **Segarkan**.
  - **5 KPI**: Total intent, Terpanggil (+% dari total), Tidak terpanggil, Kandidat retensi (idle ≥ N bln), Soft-deleted.
  - Tab filter + tabel: Intent · Bahasa · Frekuensi · Terakhir dipanggil · Idle · Status (badge) · Aksi (**Soft delete / Pulihkan**).

---

## Catatan implementasi
- **Penempatan status di Katalog Intent**: halaman `/lifecycle` adalah tampilan Katalog Intent yang difokuskan pada status siklus-hidup; sekaligus API katalog lama kini membawa field siklus-hidup.
- **Soft delete = penanda**, tidak menghapus intent dari Katalog maupun Dialogflow. Pembersihan/penghapusan nyata di Dialogflow tetap tindakan manual analis — konsisten dengan keputusan bahwa aksi pengelolaan di Camerad Studio bersifat penanda.
- **Retensi berbasis kalender** memakai `day` (tanggal UTC), cukup untuk ambang bulanan.

## Validasi
- `py_compile` `web_app.py` + `intentmap_db.py` + `analytics_db.py`: OK
- Parse Jinja seluruh **12** template: OK
- `node --check` JS halaman baru: OK
- **Smoke test fungsional** (baris uji sementara, lalu dibersihkan):
  - `refresh_lifecycle` mengisi `last_called_at`/`frekuensi_panggil` intent terpanggil (contoh: `2026-07-21`), mengosongkan yang tidak terpanggil.
  - Klasifikasi: intent tak terpanggil → kandidat retensi; intent idle 18,9 bln → status `retensi` namun tetap terhitung “terpanggil”.
  - Soft delete (via id **dan** via nama intent) + pulihkan berfungsi; intent soft-deleted keluar dari kandidat retensi.

> Catatan: FastAPI tidak terpasang di sandbox build ini, jadi verifikasi runtime akhir (jalankan `uvicorn web_app:app --host 0.0.0.0 --port 8080`, buka menu **Siklus Hidup Intent**) sebaiknya dilakukan di server Anda. Semua pemeriksaan statik & logika DB sudah lulus.

> Di server, Katalog Intent berisi ± 1.800 intent; di sandbox ini katalog kosong (di-reset), sehingga smoke test memakai baris uji sementara yang langsung dibersihkan.
