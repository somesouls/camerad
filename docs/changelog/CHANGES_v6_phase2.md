# Camerad Studio — v6 (Fase 2: Analisis Deflection & Sesi)

Fase 2 mengeksekusi Epik **D1–D3** dan **D6** dari rencana pengembangan.
Semua fitur ditempatkan di **halaman baru “Analisis Deflection & Sesi”** (`/deflection`) agar Dashboard tetap ringkas (keputusan #6). D4 & D5 sudah dikirim di v5.

## Ringkasan zona waktu (penting)
Kolom `ts` di tabel `interactions` berformat **ISO UTC** (akhiran `Z`). Untuk analisis jam, timestamp dikonversi ke **WIB (+7 jam)**. Konfirmasi empiris: distribusi jam +7 memuncak **09.00–14.00 WIB** (jam kerja KPP), sedangkan UTC mentah memuncak dini hari — jadi konversi WIB adalah yang benar.

---

## Backend — `analytics_db.py`

### Tabel baru: `candidate_status` (pelacakan tindak lanjut D6)
`phrase_norm` (PK), `phrase`, `status` (`''`/`skip`/`followup`), `note`, `updated_at`, `updated_by`, `followup_at`. Ditambahkan ke `init_db()` (+ indeks status).

### Konstanta sinyal intent
- `AGENT_1500200 = "System_System_Hubungi Agent"` — dipicu ketik **1500200**
- `AGENT_CONNECTOR = "System_System_Hubungi Agent Connector"` — dipicu **kata kunci/typo**
- `FALLBACK_1 = "System_System_Fallback Intent"`, `FALLBACK_2 = "System_System_Fallback Intent 2"`
- `_WIB` = ekspresi SQL konversi `ts` UTC → WIB.

### D1 — `session_journeys(conn, start, end, lang)`
Rekonstruksi tiap sesi (agregasi per `session_id`) lalu klasifikasi ke satu kategori dengan prioritas:
1. **Tersambung agent via 1500200** (`agent_1500200`)
2. **Tersambung agent via kata kunci/typo** (`agent_connector`)
3. **Fallback ganda / eskalasi tanpa agent** (`fallback2_no_agent`)
4. **Fallback lalu ditinggalkan** (`fallback_abandon`)
5. **Dilayani mandiri** (`self_served`)
Membedakan **fallback‑1** vs **fallback‑2**, dan menghitung sesi yang ber-fallback sebelum tersambung agent.

### D2 — `hourly_load(conn, start, end, lang, work_days, work_start, work_end)`
Matriks **7 hari × 24 jam (WIB)** untuk heatmap + distribusi per jam. Klasifikasi **jam kerja** (default Sen–Jum 08.00–16.00 WIB, dapat dikonfigurasi) vs **luar jam**, plus jam puncak.

### D3 — `service_quality(conn, start, end, lang)`
- **Self-service rate** = sesi tuntas tanpa fallback / total sesi.
- **Keandalan (reliability)** dihitung **hanya pada sesi yang benar‑benar memakai bot**, yaitu **mengecualikan** sesi yang menghubungi agent (baik via 1500200 maupun via Connector kata kunci/typo) — sesuai keputusan #5.

`deflection_overview(...)` menggabungkan D1+D2+D3 dalam satu panggilan.

### D6 — Drill-down Kandidat Intent Baru
- `candidate_list(...)` — daftar frasa fallback + status tindak lanjut.
- `candidate_detail(...)` — untuk satu frasa: jumlah kemunculan, jumlah sesi, **pecahan topik** (intent bersih yang muncul di sesi yang sama, mis. “ya” → KUP_NPWP_*), daftar sesi, dan pemantauan pasca tindak lanjut.
- `session_transcript(...)` — transkrip 1 percakapan urut waktu (tombol “Lihat percakapan”).
- `set_candidate_status(...)` / `get_candidate_statuses(...)` — tandai **Skip** / **Tindak lanjut** (hanya penanda status; pelatihan intent tetap manual di Dialogflow).
- `candidate_followup_check(...)` — **pemantauan otomatis**: setelah ditandai `followup`, menghitung apakah frasa **masih** jatuh ke fallback (masih muncul / sudah teratasi).

---

## Backend — `web_app.py` (route & API baru)
- `GET  /deflection` — halaman baru (sidebar aktif = `deflection`).
- `GET  /api/deflection/summary` — D1+D2+D3 (param: `range/start/end/lang/work_start/work_end/work_days`).
- `GET  /api/deflection/candidates` — daftar kandidat + status.
- `GET  /api/deflection/candidate?phrase=` — drill-down 1 frasa.
- `GET  /api/deflection/transcript?session_id=` — transkrip percakapan.
- `POST /api/deflection/status/save` — simpan status Skip/Tindak-lanjut (mencatat `updated_by` dari sesi login).

## Frontend
- `templates/base.html` — menu sidebar baru **“Analisis Deflection”** (aktif via `active_page`).
- `templates/deflection.html` — halaman baru: toolbar (rentang, bahasa, konfigurasi jam kerja), **KPI D3**, **bar jejak sesi D1**, **heatmap jam × hari + distribusi per jam D2**, dan **tabel kandidat D6** dengan modal drill-down (pecahan topik, daftar sesi, tombol Skip/Tindak-lanjut, viewer transkrip, indikator pemantauan).

---

## Hasil smoke test (rentang 2026-06-23 → 2026-07-23)
- **D1**: 45.991 sesi — 50,1% tersambung agent via 1500200; 41,5% dilayani mandiri; 7,1% fallback lalu ditinggalkan; 0,7% via kata kunci/typo; 0,6% eskalasi fallback‑2.
- **D2**: puncak **jam 10.00 WIB**; **73,1%** interaksi terjadi dalam jam kerja.
- **D3**: self-service **41,5%**; **keandalan bot 84,33%** (dari 22.636 sesi yang benar‑benar memakai bot).
- **D6**: “ya” (117×) → pecah ke `KUP_NPWP_Konfirmasi NPWP`, `KUP_NPWP_Tata Cara Pendaftaran`, dst. Transkrip, penandaan status, dan pemantauan pasca tindak-lanjut berfungsi.

## Validasi
- `py_compile` `web_app.py` + `analytics_db.py`: OK
- Parse Jinja seluruh 11 template: OK
- `node --check` JS halaman baru: OK
- Smoke test DB fungsi D1–D3 & D6: OK

> Catatan: FastAPI tidak terpasang di sandbox build ini, jadi verifikasi runtime akhir (jalankan `uvicorn web_app:app --host 0.0.0.0 --port 8080`) sebaiknya dilakukan di server Anda. Semua pemeriksaan statik & logika DB sudah lulus.
