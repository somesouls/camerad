# camerad — Chatbot Pajak + RAG Peraturan + Avaya AWE (Lokal, 100% Python)

[![CI](https://github.com/somesouls/camerad/actions/workflows/ci.yml/badge.svg)](https://github.com/somesouls/camerad/actions/workflows/ci.yml)

Aplikasi **FastAPI** untuk analisis Dialogflow (Step 1–11) + Avaya (Step 12–16), **chat AI berbasis RAG peraturan pajak**, dan analitik AWE — **berjalan penuh di lokal / LAN** dengan **LLM cloud** (OpenAI / Gemini / Azure OpenAI).

> **Struktur kode:** repo ini sudah ditata menjadi **paket Python per-domain** (bukan lagi berkas datar di root). Peta paket & konvensi kontribusi ada di **`AGENTS.md`**; detail arsitektur & riwayat migrasi di **`docs/ARCHITECTURE.md`**.

> **Sejarah:** frontend yang dulu `index.php` (PHP) sudah di-port ke Python (FastAPI → `web_app.py`); tampilan & alur identik. Versi PHP lama disimpan di `_legacy/index.php` sebagai referensi. Halaman utama `/` berupa **landing page + Chat AI**; tool analisis Step 1–16 ada di route **`/tools`**.

---

## Arsitektur (ringkas)

Untuk pemakaian harian, **satu proses** sudah cukup:

```
        Browser (PC lain di LAN)
               |  http://<IP-PC>:8080/
               v
   web_app.py  (FastAPI: UI + RAG + Avaya AWE)     <-- port 8080, SATU proses
     - Template UI            (templates/, static/)
     - Mesin RAG peraturan    (paket rag/, peraturan/)
     - Avaya AWE              (paket avaya/, awe/)  -> bootstrap in-process
     - Panggilan LLM -> cloud (paket common/)
```

- Mesin **RAG** dan **Avaya AWE** kini **ter-bootstrap langsung di dalam `web_app.py`**, sehingga operasi harian cukup **satu terminal / satu proses** (port 8080).
- `llm_fix_final_combined.py` (port 8000) adalah **backend berat lama** yang sekarang **opsional** — hanya diperlukan untuk sebagian pekerjaan Step Dialogflow tertentu. `start.bat` tidak menjalankannya otomatis; `start.sh` menyalakannya hanya bila diberi flag `--with-backend`.
- Semua komputasi model tetap **lokal** (`127.0.0.1`). Tidak ada ngrok / Colab / Google Drive.

---

## Backend vs Frontend — apakah perlu dipisah?

**Saat ini keduanya menyatu di `web_app.py` (satu proses, port 8080)** untuk operasi harian, baik di Windows (`start.bat`) maupun Linux/macOS (`start.sh`). Backend berat lama (`llm_fix_final_combined.py`, port 8000) opsional dan hanya untuk sebagian Step Dialogflow: nyalakan dengan `./start.sh --with-backend` (Linux) atau jalankan manual di terminal terpisah (Windows).

**Perlu dipisah?** Untuk deployment satu mesin / LAN internal seperti sekarang, **tidak perlu**. Satu proses lebih sederhana dan hemat memori (model reranker + indeks QA hanya di-load sekali). Pemisahan baru berguna bila: (a) mau restart UI tanpa unload model besar, (b) UI dan model jalan di mesin berbeda (mis. GPU box terpisah), atau (c) mau scaling terpisah.

---

## Struktur paket

Repo ditata **per-domain** (kohesi tinggi, kopling rendah). Ringkasannya:

| Paket / berkas | Isi |
|---|---|
| `web_app.py` | **Entry point utama** (FastAPI: UI + RAG + Avaya). |
| `templates/`, `static/` | Presentasi: `index.html` (landing + Chat AI), `tools.html` (Step 1–16), `dashboard.html`, `data.html`, `glossary.html`, `disambig.html`, `intentmap.html`, dst. |
| `routes/` | Lapisan HTTP/menu (system, studio, analytics, auth, data, audit_tp, ...). |
| `rag/`, `peraturan/` | Mesin RAG peraturan (engine, router, reranker, rewrite) + parser/semantic korpus. |
| `avaya/`, `awe/` | Integrasi & analitik Avaya AWE. |
| `chat/` | Endpoint chat frontend & agent. |
| `knowledge/` | Glosarium, disambiguasi, peta intent, konteks pengetahuan (`match`/`build_context_text`), stats. |
| `db/` | Lapisan penyimpanan SQLite per subsistem. |
| `common/` | Util lintas-domain (LLM client, normalisasi teks, PII mask, referensi peraturan, dll). |
| `pipeline/`, `evaluation/`, `handoff/`, `df_webhook/`, `sop/`, `sosmed/`, `pustaka/` | Domain pendukung lainnya. |
| `phase0/1/2_upgrade.py`, `phase4_eval.py`, `phase5_qa_build.py`, `ingest.py`, `docstudio.py`, `llm_fix_final_combined.py` | Entry-point skrip yang **tetap di root** (dijalankan langsung sebagai `python <file>`). |
| `scripts/` | `oneoff/` (util aktif: `check_structure.py`, `install.py`, `cek_db.py`) + `_archive/` (skrip reorg/migrasi yang sudah selesai). |
| `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `scripts/oneoff/check_structure.py` | **Guard** CI + pre-commit: `py_compile` semua `.py` + cegah shim baru di root. |
| `_legacy/index.php` | Versi PHP lama (referensi saja). |

> Peta modul **lengkap** (nama tiap modul di dalam paket) + konvensi kontribusi ada di **`AGENTS.md`** dan **`docs/ARCHITECTURE.md`**. Prinsip: jangan tambah modul datar / shim baru di root — CI akan menolaknya.

---

## Sistem Analitik (dashboard + AI tanya-jawab data)

Selain tools sekali-proses, sistem ini menyimpan setiap interaksi Dialogflow ke
database **SQLite** untuk analisis periodik.

**Pembagian peran (arsitektur holistik):**
- **Kelola Data** (`/data`) = satu-satunya tempat **menarik & menyimpan** data ke database. Pilih tanggal → cek status kelengkapan → tarik.
- **Dashboard** (`/dashboard`) = **read-only**, hanya memilih periode & menampilkan (tidak menarik data).
- **Tools Step 1** (`/tools`) = ikut **memakai database yang sama**. Kalau data sudah lengkap, Step 1 **memuat dari database** (tanpa tarik ulang) lalu membangun ulang output JSON yang identik.

**Cara kerja ingest (hybrid + pintar):**
- **Otomatis** tiap hari **jam 08:00 (Asia/Jakarta)** menarik data **H-1**. Atur via `PIPELINE_INGEST_HOUR/MINUTE`, matikan dengan `PIPELINE_SCHEDULER=0`.
- **Manual** kapan saja lewat halaman **Kelola Data**, atau CLI:
  ```bash
  python ingest.py --yesterday          # data kemarin
  python ingest.py --days 30            # 30 hari terakhir (hanya hari yang belum lengkap)
  python ingest.py --start 2026-07-01 --end 2026-07-15
  python ingest.py --days 30 --force    # paksa tarik ulang semua hari
  ```
- **Ingest pintar**: hanya menarik hari yang **belum ada / belum lengkap**. Anti-duplikat otomatis (berdasarkan `insertId`).

**Mekanisme kelengkapan:**
- Status per hari: **✅ Lengkap**, **⚠ Partial**, **⬜ Belum ada**.
- Sebuah hari ditandai **Lengkap hanya jika hari itu sudah lewat** saat ditarik. Data yang ditarik di tengah hari → **Partial**, dan otomatis ditarik ulang keesokan harinya oleh penjadwal 08:00.

**Dashboard** (`/dashboard`) menampilkan, dengan filter rentang **Hari ini / Kemarin / 7 / 30 / 90 hari / Semua / Custom**:
- KPI: total interaksi, sesi, fallback + rate, intent unik
- Intent paling banyak terpanggil; volume interaksi per hari (total vs fallback)
- **Pertanyaan baru tanpa intent** (kandidat intent baru dari fallback)
- Topik terhangat; pencarian “intent terkait topik X”
- Indikator kelengkapan periode terpilih

**AI tanya-jawab data** (kotak di paling atas dashboard): tanya bebas seperti *“Apa 5 intent terbanyak minggu lalu?”*. AI mengubah pertanyaan jadi query SQL **read-only** ke database lalu menjawab dengan angka asli (bukan mengarang).

> ⚠ **Retensi log:** Google Cloud Logging menyimpan log **~30 hari** default. Data masa lampau hanya bisa ditarik sejauh log masih tersedia. Untuk riwayat panjang, siapkan sink ke BigQuery / log bucket beretensi panjang. Setelah masuk database, data tetap tersimpan walau di sumber sudah terhapus.

---

## Glosarium Istilah Perpajakan (konteks untuk analisis)

Model LLM sering **tidak paham istilah pajak Indonesia** yang baru/khusus (Coretax, BPPU, Kode Otorisasi DJP). Glosarium menyuntikkan definisi istilah sebagai konteks saat analisis fallback & MKTA, sehingga model tidak menebak/mengarang.

**Halaman Kelola Istilah** (`/glossary`):
- Tabel istilah dengan pencarian & filter (kategori / sistem / status).
- **Tambah / edit / hapus** istilah sendiri (tanpa ngoding).
- **Petunjuk pengisian lengkap** ada di halaman (aturan istilah gabungan, penjelasan tiap kolom, prinsip anti-halusinasi).
- Saat pertama dibuka, otomatis terisi **30 istilah contoh** (SPT, NPWP, EFIN, PPN, Faktur Pajak, Coretax, dll).

**Aturan istilah gabungan:** istilah seperti **SPT Masa** dan **SPT Tahunan** ditulis **utuh sebagai entri sendiri** (definisi & masalahnya berbeda), dengan kata dasar (**SPT**) di field *istilah terkait*. Variasi penyebutan cukup jadi **alias**.

**Anti-halusinasi:** setiap istilah punya penanda **Terverifikasi**. Istilah baru yang belum dicek diberi tanda ⚠ dan definisinya diawali `[PERLU VERIFIKASI TIM]`. Untuk tugas analisis, setel **temperature rendah (0–0.1)**.

Route API: `GET /api/glossary/list`, `POST /api/glossary/save`, `POST /api/glossary/delete`.

---

## Pustaka Disambiguasi & Routing (frasa ambigu)

Menangani **frasa user yang ambigu / bercabang** — satu kalimat yang bisa berarti beberapa hal — dan membantu AI memilih makna yang benar saat menganalisis fallback & MKTA.

**Contoh:** `lupa password` → DJP Online **atau** Coretax; `minta efin` → lupa/aktivasi/reset EFIN; `lapor pajak`/`bayar pajak`/`buat faktur` → alur lama vs Coretax.

**Halaman Kelola Disambiguasi** (`/disambig`):
- Tabel aturan + pencarian/filter, tambah/edit/hapus sendiri.
- Petunjuk pengisian lengkap di halaman (format kandidat `label | sistem | intent_bot | petunjuk`).
- Saat pertama dibuka otomatis terisi **7 aturan contoh**.

**Aturan waktu (temporal routing) — khusus DJP:** Coretax berlaku **Januari 2025**. Cutoff bisa diatur per-aturan. Untuk hal lintas-sistem (mis. EFIN), aturan waktu dimatikan dan bot diarahkan mengajukan **pertanyaan klarifikasi**.

Route API: `GET /api/disambig/list`, `POST /api/disambig/save`, `POST /api/disambig/delete`. Modul disambiguasi (paket `knowledge/`) menyediakan `match(query, tanggal)` dan `build_context_text(...)` yang siap dipakai mesin analisis.

---

## Peta Intent & Maksud Analis (kunci jawaban analis)

Pengetahuan **jenis ketiga** (setelah Glosarium & Disambiguasi): **keputusan sengaja analis** dalam memetakan pertanyaan user ke intent Dialogflow — terutama yang **melawan logika semantik biasa**, sehingga LLM mustahil menebaknya. AI memakainya sebagai **“kunci jawaban”** saat menilai fallback & MKTA.

**Contoh:**
- `lupa email dan no hp` → intent **Perubahan Data** (bukan sekadar keluhan login).
- Semua pertanyaan **UMKM** → satu **megaintent** `UMKM` + **bridging intent anakan**, karena memecahnya jadi banyak intent mirip berisiko menaikkan fallback/MKTA. AI perlu tahu ini agar **tidak menyarankan memecah UMKM**.

**Halaman Peta Intent** (`/intentmap`):
- Tabel kebijakan + cari/filter, tambah/edit/hapus sendiri.
- Struktur per intent: `mandiri` / `megaintent` (+ daftar bridging) / `bridging_child` (+ induk).
- Kolom **Cakupan**, **Contoh utterance**, **BUKAN ini**, **Alasan/maksud analis** (wajib), **Batasan Dialogflow**.
- **Prioritas 5 tingkat** (Sangat Tinggi → Sangat Rendah) untuk menentukan aturan mana menang saat beberapa cocok.
- Petunjuk pengisian lengkap + otomatis terisi **5 contoh** saat pertama dibuka.

### 🔗 Penyambungan ketiga pustaka ke mesin analisis

Ketiga pustaka (Glosarium, Disambiguasi, Peta Intent) **otomatis dipakai** oleh AI saat menganalisis — bukan sekadar disimpan. Modul konteks pengetahuan (paket `knowledge/`) mencari entri relevan tiap pertanyaan (lewat `match()`) lalu menyuntikkan ringkasannya ke *prompt* LLM. Titik yang tersambung:

- **Step 5 — pemilihan intent** (`build_prompt`).
- **Step 8 — putusan MKTA** (`MKTA_VERDICT_PROMPT`).
- **Chat** halaman utama (`/api/chat`).
- **AI tanya-data** (`/api/ask-data`).

Prinsip: hanya entri yang cocok yang disuntik (ringkas & terstruktur), diberi label **acuan internal tim**. Bila pustaka kosong/tidak relevan, konteks kosong (aman). Temperature tahap analitis dijaga rendah (0–0.2).

> **Disambiguasi DJP Online vs Coretax berbasis MASA PAJAK.** Penentu sistem adalah **masa pajak** yang ditanyakan user (mis. "lapor SPT masa Desember 2024"), **bukan** tanggal user bertanya. Jika masa pajak tidak disebut, kasus ditandai **AMBIGU** dan disarankan klarifikasi.

### Sampai batas mana LLM perlu “diberi tahu”?
Kodifikasikan **HANYA** keputusan yang (a) tak bisa disimpulkan dari teks + akal sehat, DAN (b) kalau salah paham mengubah kesimpulan analisis. Sisanya biarkan LLM. Setiap aturan adalah beban pemeliharaan — mulai dari intent kontra-intuitif & bervolume tinggi (Perubahan Data, UMKM), tumbuhkan dari pola fallback nyata.

Route API: `GET /api/intentmap/list`, `POST /api/intentmap/save`, `POST /api/intentmap/delete`. Modul peta intent (paket `knowledge/`) menyediakan `match(query)` dan `build_context_text(...)`.

---

## Cara jalan (tanpa Docker) — disarankan

### 1. Prasyarat
- **Python 3.10+** (3.11/3.12 disarankan). Cek: `python --version`. Tidak perlu PHP.

### 2. Install dependensi
**Linux/macOS:** `chmod +x install.sh start.sh && ./install.sh`
**Windows:** `install.bat`
Skrip membuat virtualenv `.venv` + meng-install `requirements.txt`.

> **Punya GPU NVIDIA?** Ganti torch CPU dengan versi CUDA:
> ```bash
> pip uninstall -y torch
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ```
> Backend otomatis memakai GPU bila `torch.cuda.is_available()`.

### 3. Konfigurasi
```bash
cp .env.example .env      # Windows: copy .env.example .env
```
Edit `.env`: `LLM_PROVIDER=openai` (atau `gemini`/`azure`) + API key; `PIPELINE_API_KEY` boleh default; `WEB_PORT=8080`.

### 4. (Opsional) Google service-account
Step 1 (tarik log Dialogflow) & Step 3/13 (tarik intent) butuh akses Google Cloud. Taruh `service-account.json` di folder yang sama dengan `web_app.py`, atau set `PIPELINE_SA_FILE=...` di `.env`, atau tempel Access Token manual di form. File ini **tetap lokal** (sudah masuk `.gitignore`).

### 5. Jalankan
- **Windows:** `start.bat` → **satu proses** `web_app.py` (UI + RAG + Avaya) di port 8080.
- **Linux/macOS:** `./start.sh` → satu proses `web_app.py`. Tambah `--with-backend` bila butuh backend 8000 lama.
- **Langsung:** `python web_app.py` (cukup untuk operasi harian).

### 6. Akses dari PC lain di LAN
1. Cari IP PC ini: `hostname -I` (Linux) / `ipconfig` (Windows).
2. Buka di PC lain: **`http://<IP-PC-INI>:8080/`**
3. Izinkan port **8080** di firewall:
   - Windows: *Windows Defender Firewall → Inbound Rule → Port 8080 → Allow*.
   - Linux (firewalld): `sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload`
   - Linux (ufw): `sudo ufw allow 8080/tcp`

---

## Cara jalan via Docker (opsional)

```bash
cp .env.example .env      # isi API key
# (opsional) taruh service-account.json di folder ini
docker compose up --build
```
Akses: `http://<IP-PC-HOST>:8080/`. Image mengunduh torch CPU + model HuggingFace saat pertama kali; cache disimpan di volume `hf_cache`.

---

## Alur Step

**Dialogflow:** 1) Tarik log → 2) JSON→XLSX → 3) Training & Intent → 4) Analisis rekomendasi → 5) Judgement LLM → 6) Cross-check manual → 7) Analisis MKTA → 8) Putusan LLM MKTA → 9) Analisis manual MKTA → 10) Laporan LM & Pembaruan → 11) Pembaruan intent (usersays).

**Avaya:** 12) Upload JSON → 13) Tarik intent → 14) Analisis → 15) Dashboard → 16) Ekspor Excel.

Setiap step berdiri sendiri; hasil step yang sukses tersimpan di `_runs/<run>/` dan bisa dipakai step berikutnya tanpa upload ulang.

---

## Variabel lingkungan penting

| Var | Default | Fungsi |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` / `gemini` / `azure` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Kredensial OpenAI |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-1.5-flash` | Kredensial Gemini |
| `AZURE_OPENAI_*` | — | Kredensial Azure OpenAI |
| `PIPELINE_API_KEY` | `sam-n8n-secret` | Kunci internal frontend↔backend (opsional 8000) |
| `PIPELINE_PORT` / `PIPELINE_API_HOST` | `8000` / `127.0.0.1` | Bind backend lama (opsional) |
| `PIPELINE_API_BASE` | `http://127.0.0.1:8000` | Alamat backend yg dipanggil frontend |
| `PIPELINE_FORCE_LOCAL` | `1` | Selalu pakai backend lokal |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8080` | Bind web UI |
| `RAG_MIN_COS` | `0.65` | Ambang minimal kemiripan RAG |
| `PERATURAN_EMBED_MODEL` | `BAAI/bge-m3` | Model embedding korpus peraturan |
| `RAG_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Model reranker RAG |
| `PIPELINE_SA_FILE` | `./service-account.json` | Path service account Google |
| `PIPELINE_PROJECT_ID` | `avaya-djp-klipbot-prod` | Project Dialogflow |
| `PIPELINE_RUNS_DIR` | `./_runs` | Folder hasil kerja |

---

## Troubleshooting

- **Web UI kebuka tapi step Dialogflow berat gagal “connection refused”** → backend opsional (port 8000) belum dinyalakan/siap. Untuk step tsb, jalankan `./start.sh --with-backend` (Linux) atau `python llm_fix_final_combined.py` di terminal terpisah (Windows).
- **Step 1/3 gagal “service-account.json tidak ditemukan”** → taruh file SA atau tempel Access Token di form.
- **PC lain tidak bisa buka** → cek firewall port 8080 & pastikan `WEB_HOST=0.0.0.0`.
- **Torch lambat / ingin GPU** → install torch versi CUDA (lihat langkah 2).
- **Mau kontribusi kode** → baca `AGENTS.md` dulu; jangan tambah modul datar/shim baru di root (ditolak guard CI). Jalankan `python scripts/oneoff/check_structure.py` sebelum commit.
