# Camerad Studio — Catatan Perubahan v5 (Fase 1)

Fase 1 dari Rencana Pengembangan Dashboard Analitik. Mencakup **Epik A** (filter Include System/Umum) + **D4** (Tren Volume: scrollable + match rate + % di batang) + **D5** (pagination Distribusi Intent Teratas & Kandidat Intent Baru).

## Ringkasan

### Epik A — Filter "intent bersih" (Include System / Include Umum)
- Dashboard sekarang **secara bawaan mengecualikan** intent berawalan `System_` (termasuk Welcome, Fallback, Fallback 2, Hubungi Agent) dan `Umum_`, sehingga menampilkan **intent bersih**.
- Dua centang baru di toolbar: **Include System** dan **Include Umum** (keduanya default OFF). Mencentang salah satunya langsung memuat ulang data.
- Pemfilteran dilakukan **saat query** berbasis awalan nama (`substr(intent_name,1,7) <> 'System_'`, `substr(...,1,5) <> 'Umum_'`) — **tidak mengubah** kolom `is_system` / `classify()` yang lama. Aman dari wildcard SQL (tanpa `LIKE`/`ESCAPE`).
- Berpengaruh pada: **Distribusi Intent Teratas** dan KPI **Intent Terpanggil** (distinct intents). KPI volume (Total, Sesi, Fallback) tetap menghitung seluruh trafik.
- Katalog intent internal (`_intent_freq_map`) tetap mengambil SELURUH intent (`include_system=True, include_umum=True`) — tidak terpengaruh toggle dashboard.

### D4 — Tren Volume Interaksi
- Grafik kini **bisa digeser mendatar** (scroll) untuk rentang panjang: tiap batang lebar tetap 30px, tidak lagi memampat.
- **Match rate harian** ditampilkan sebagai **% di atas tiap batang** (porsi yang dikenali mesin = (total − fallback) / total).
- Tooltip diperkaya: Total, Dikenali (+% match rate), dan jumlah Fallback.

### D5 — Pagination
- **Distribusi Intent Teratas**: kini menarik hingga 100 intent, ditampilkan 15/halaman dengan navigasi Sebelumnya/Berikutnya. Skala batang konsisten (relatif nilai tertinggi keseluruhan).
- **Kandidat Intent Baru**: kini menarik hingga 200 kandidat, ditampilkan 25/halaman. Nomor peringkat mengikuti offset halaman.

## Berkas yang diubah
- `analytics_db.py` — helper `_class_expr` / `_apply_class`; parameter `include_system` & `include_umum` pada `overview()` dan `top_intents()`.
- `web_app.py` — `analytics_summary()` meneruskan toggle + menaikkan limit (top_intents 100, new_questions 200); `api_analytics_summary()` membaca query `inc_system` / `inc_umum`; `_intent_freq_map()` memakai `include_umum=True`.
- `templates/dashboard.html` — dua centang toolbar; `qs()` mengirim `inc_system` / `inc_umum`; render Tren Volume (scroll + % match rate); pagination client-side untuk Distribusi Intent Teratas & Kandidat Intent Baru; CSS `.pct`, `.pager`, `.chk`.

## Validasi
- `python3 -m py_compile analytics_db.py web_app.py` — OK.
- Smoke test DB nyata (rentang 2026-06-23 → 07-23): default mengecualikan `System_`/`Umum_`; centang mengembalikannya; overview bersih 539 vs penuh 558. OK.
- `node --check` blok JS dashboard — OK. Parsing Jinja seluruh template — OK.

## Catatan
- FastAPI tidak terpasang di sandbox validasi; verifikasi runtime penuh lewat `uvicorn web_app:app --host 0.0.0.0 --port 8080` di server Anda.
- `analytics.db` (1,1 GB) TIDAK disertakan dalam zip; `users.db` disertakan.
