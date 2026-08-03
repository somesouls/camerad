# Camerad Studio — Statistik pemakaian per-baris + pagination (v3)

Lanjutan dari v2 (rail sidebar, rebrand, disetujui_oleh, kartu statistik dashboard).

## 1. Badge "dipakai N×" per-baris (4 pustaka)
Setiap baris pada keempat daftar kini menampilkan badge kecil berapa kali entri
tersebut terpakai saat analisis/chat:
- Glosarium Pajak (templates/glossary.html)
- Disambiguasi (templates/disambig.html)
- Peta Intent (templates/intentmap.html — tabel kebijakan)
- Katalog Intent (templates/intentmap.html — tabel katalog)

Sumber angka: tabel pustaka_pemakaian di analytics.db (modul pustaka_stats.py),
diisi otomatis oleh knowledge_ctx.py setiap pustaka dipakai membangun konteks.

### Server (web_app.py)
- Helper _enrich_dipakai(items, pustaka) menambahkan field dipakai (int) ke tiap
  item, aman-gagal (try/except; 0 bila belum ada data).
- Dipanggil di 4 endpoint daftar: /api/glossary/list, /api/disambig/list,
  /api/intentmap/list, /api/intentmap/catalog.

### Template
- Fungsi usedBadge(it) merender chip kuning "dipakai N×" (hanya bila N>0),
  disisipkan pada sel nama tiap baris.

## 2. Pagination daftar (biar rapi)
Keempat daftar dipaginasi 15 baris/halaman dengan kontrol Sebelumnya/Berikutnya +
info "Hal X / Y · a–b dari total".
- makePager(tbodyId, pagerId, size): tidak mengubah fungsi render; semua baris
  tetap ada di DOM (handler edit/hapus/setujui tetap jalan di semua halaman),
  pagination hanya toggle display. MutationObserver mendeteksi render ulang
  (search/filter) lalu reset ke halaman 1. Aman-gagal (try/catch).
- Kartu statistik agregat di Dashboard tetap dipertahankan.

## Catatan
- Angka dipakai bertambah setelah aplikasi dijalankan & pustaka benar-benar
  terpakai; DB otomatis dibuat/migrasi saat pertama run.
- Verifikasi runtime: uvicorn web_app:app --host 0.0.0.0 --port 8080
