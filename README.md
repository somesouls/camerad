# Dialogflow + Avaya Pipeline (Lokal, 100% Python)

Pipeline analisis Dialogflow (Step 1–11) + Avaya (Step 12–16) yang **berjalan penuh di lokal / LAN** dan memakai **LLM cloud** (OpenAI / Gemini / Azure OpenAI).

> **Perubahan versi ini:** frontend web yang dulu `index.php` (PHP) sudah **di-port ke Python (FastAPI)** → `web_app.py`. **Struktur, cara kerja, dan tampilan 100% identik** dengan versi PHP — hanya *framework* server-nya yang berganti. Tidak perlu memasang PHP lagi. Tampilan tool analisis kini ada di `templates/tools.html`.

> **Baru:** halaman utama `/` sekarang berupa **landing page + Chat AI** (ala ChatGPT/Gemini/Claude) di `templates/index.html`. Tool analisis Step 1–16 pindah ke route **`/tools`**. Chat memanggil LLM cloud langsung (`/api/chat`) dan **riwayatnya disimpan di browser** (localStorage) — tidak butuh backend `:8000` menyala.

---

## Arsitektur

```
            Browser (PC lain di LAN)
                     |  http://<IP-PC>:8080/
                     v
   web_app.py  (FastAPI frontend, pengganti index.php)   <-- port 8080
                     |  HTTP internal (X-API-Key)
                     v
   llm_fix_final_combined.py (FastAPI backend berat)     <-- port 8000 (127.0.0.1)
     - SBERT / BGE reranker / NLI / QA (pakai torch)
     - Rute Avaya (avaya_pipeline.py + patch)
     - Panggilan LLM -> cloud (llm_client.py)
```

- **Frontend** (`web_app.py`) menyajikan UI + logika ringan: tarik log Google, konversi XLSX, cross-check manual, laporan, dan **mem-proxy** proses berat ke backend.
- **Backend** (`llm_fix_final_combined.py`) menjalankan model lokal (torch) + memanggil LLM cloud.
- Semua komunikasi model berat tetap **lokal** (`127.0.0.1:8000`). Tidak ada ngrok / Colab / Google Drive.

---

## Isi paket

| File | Fungsi |
|---|---|
| `web_app.py` | **Frontend FastAPI** (pengganti `index.php`). UI + Step 1–16. |
| `templates/index.html` | **Landing page + Chat AI** (halaman utama `/`). |
| `templates/tools.html` | Tool analisis Step 1–16 (route `/tools`) — identik dengan `index.php`. |
| `templates/dashboard.html` | **Dashboard analitik** — hanya pilih periode & tampilkan (route `/dashboard`). AI tanya-jawab data ada di paling atas. |
| `templates/data.html` | **Kelola Data** — input tanggal, cek kelengkapan, tarik & simpan ke database (route `/data`). |
| `templates/glossary.html` | **Kelola Istilah** — glosarium perpajakan, CRUD + petunjuk pengisian (route `/glossary`). |
| `glossary_db.py` | Lapisan database SQLite glosarium + validasi + 30 istilah contoh (seed). |
| `templates/disambig.html` | **Kelola Disambiguasi** — frasa ambigu, cabang makna, aturan waktu (route `/disambig`). |
| `disambig_db.py` | Lapisan database SQLite disambiguasi + routing temporal (Coretax/DJP Online) + 7 aturan contoh. |
| `templates/intentmap.html` | **Peta Intent & Maksud Analis** — kebijakan pemetaan intent + struktur megaintent/bridging (route `/intentmap`). |
| `intentmap_db.py` | Lapisan database SQLite peta intent (maksud analis) + `match()`/`build_context_text()` + 5 contoh. |
| `analytics_db.py` | Lapisan database SQLite + query analitik + text-to-SQL aman + pelacakan kelengkapan per hari. |
| `ingest.py` | Skrip tarik log Dialogflow → database (ingest pintar per hari, manual/terjadwal). |
| `llm_fix_final_combined.py` | Backend FastAPI (analisis berat + LLM cloud). |
| `llm_client.py` | Adapter LLM cloud: OpenAI / Gemini / Azure. |
| `avaya_pipeline.py`, `avaya_speedpatch.py`, `avaya_dashpatch.py` | Modul & patch analisis Avaya. |
| `requirements.txt` | Dependensi Python. |
| `.env.example` | Contoh konfigurasi (salin ke `.env`). |
| `install.sh` / `install.bat` | Buat venv + install dependensi. |
| `start.sh` / `start.bat` | Jalankan backend + frontend sekaligus. |
| `Dockerfile`, `docker-compose.yml`, `start_docker.sh` | Opsi jalan via Docker. |
| `_legacy/index.php` | Versi PHP lama (referensi saja, tidak dipakai). |

---

## Sistem Analitik (dashboard + AI tanya-jawab data)

Selain tools sekali-proses, sistem ini menyimpan setiap interaksi Dialogflow ke
database **SQLite** (`analytics.db`) untuk analisis periodik.

**Pembagian peran (arsitektur holistik):**
- **Kelola Data** (`/data`) = satu-satunya tempat **menarik & menyimpan** data ke database. Pilih tanggal → cek status kelengkapan → tarik.
- **Dashboard** (`/dashboard`) = **read-only**, hanya memilih periode & menampilkan (tidak menarik data).
- **Tools Step 1** (`tools.html`) = ikut **memakai database yang sama**. Kalau data sudah lengkap, Step 1 **memuat dari database** (tanpa tarik ulang) lalu membangun ulang output JSON yang identik.

**Cara kerja ingest (hybrid + pintar):**
- **Otomatis** tiap hari **jam 08:00 (Asia/Jakarta)** menarik data **H-1** (kemarin). Atur via `PIPELINE_INGEST_HOUR/MINUTE`, matikan dengan `PIPELINE_SCHEDULER=0`.
- **Manual** kapan saja lewat halaman **Kelola Data**, atau CLI:
  ```bash
  python ingest.py --yesterday          # data kemarin
  python ingest.py --days 30            # 30 hari terakhir (hanya hari yang belum lengkap)
  python ingest.py --start 2026-07-01 --end 2026-07-15
  python ingest.py --days 30 --force    # paksa tarik ulang semua hari
  ```
- **Ingest pintar**: hanya menarik hari yang **belum ada / belum lengkap**. Hari yang sudah lengkap dilewati (hemat kuota).
- Anti-duplikat otomatis (berdasarkan `insertId`), jadi aman ditarik berulang.

**Mekanisme kelengkapan (database harus lengkap):**
- Status per hari: **✅ Lengkap**, **⚠ Partial**, **⬜ Belum ada** — bisa dicek di halaman Kelola Data.
- Sebuah hari ditandai **Lengkap hanya jika hari itu sudah lewat** (kemarin ke belakang) saat ditarik.
- Data yang ditarik **di tengah hari** (hari ini masih berjalan) → **Partial**, dan **otomatis ditarik ulang** keesokan harinya oleh penjadwal 08:00 agar menjadi lengkap.
- Hari Partial diisi bertahap tanpa duplikat, sehingga database konvergen ke kondisi lengkap.

**Halaman Kelola Data** (`http://localhost:8080/data`):
- Pilih preset (Hari ini / Kemarin / 7 / 30 hari) atau rentang tanggal custom + bahasa.
- **Cek status** → tampil grid per hari (lengkap / partial / belum ada) + ringkasan.
- **Tarik data (pintar)** atau centang **Paksa tarik ulang** untuk menarik ulang semua hari.

**Dashboard** (`http://localhost:8080/dashboard`) menampilkan, dengan filter
rentang **Hari ini / Kemarin / 7 / 30 / 90 hari / Semua / Custom**:
- KPI: total interaksi, sesi, fallback + rate, intent unik
- Intent paling banyak terpanggil
- Volume interaksi per hari (total vs. fallback)
- **Pertanyaan baru tanpa intent** (kandidat intent baru dari fallback)
- Topik terhangat (kata kunci tersering)
- Pencarian “intent terkait topik X”

Dashboard juga menampilkan **indikator kelengkapan** periode terpilih; jika ada
hari partial/kosong, ada ajakan untuk melengkapinya di halaman Kelola Data.

**AI tanya-jawab data** (kotak di **paling atas** dashboard): tanya bebas seperti *“Apa 5 intent
terbanyak minggu lalu?”* atau *“Berapa fallback rate 7 hari terakhir?”*. AI
mengubah pertanyaan jadi query SQL **read-only** ke database lalu menjawab dengan
angka asli (bukan mengarang).

> ⚠ **Retensi log:** Google Cloud Logging menyimpan log **~30 hari** secara
> default. Data “masa lampau” hanya bisa ditarik sejauh log masih tersedia di
> sumber. Untuk riwayat lebih panjang, siapkan **sink ke BigQuery / log bucket**
> beretensi panjang di Google Cloud (di luar cakupan skrip ini). Setelah masuk
> database, data tetap tersimpan selamanya walau di sumber sudah terhapus.

---

## Glosarium Istilah Perpajakan (konteks untuk analisis)

Model LLM (GPT-4o mini, Qwen, dll) sering **tidak paham istilah pajak Indonesia**,
terutama yang baru/khusus seperti **Coretax, BPPU, Kode Otorisasi DJP**. Glosarium
ini menyuntikkan definisi istilah sebagai konteks saat analisis fallback & MKTA,
sehingga model tidak menebak/mengarang.

**Halaman Kelola Istilah** (`http://localhost:8080/glossary`):
- Tabel istilah dengan **pencarian & filter** (kategori / sistem / status).
- **Tambah / edit / hapus** istilah sendiri (tim tidak perlu ngoding).
- **Petunjuk pengisian lengkap** ada di halaman (klik “Petunjuk pengisian”) — termasuk
  aturan istilah gabungan, penjelasan tiap kolom, dan prinsip anti-halusinasi.
- Saat pertama dibuka, otomatis terisi **30 istilah contoh** (SPT, NPWP, EFIN, PPN,
  Faktur Pajak, Coretax, dll) yang bisa langsung kamu sesuaikan.

**Aturan istilah gabungan:** istilah seperti **SPT Masa** dan **SPT Tahunan**
ditulis **utuh sebagai entri sendiri** (karena definisi & masalahnya berbeda),
dengan kata dasar (**SPT**) dicantumkan di field *istilah terkait*. Variasi
penyebutan biasa cukup dimasukkan sebagai **alias**, bukan entri baru.

**Anti-halusinasi:** setiap istilah punya penanda **Terverifikasi**. Istilah baru
yang belum dicek (mis. BPPU, Kode Otorisasi DJP) diberi tanda ⚠ dan definisinya
diawali `[PERLU VERIFIKASI TIM]` — mohon dilengkapi dari sumber resmi sebelum
diandalkan. Untuk tugas analisis, setel **temperature rendah (0–0.1)** agar hasil
konsisten & minim mengarang.

Data glosarium disimpan di tabel `glossary` pada database yang sama
(`PIPELINE_DB_FILE`). Route API: `GET /api/glossary/list`, `POST /api/glossary/save`,
`POST /api/glossary/delete`.

---

## Pustaka Disambiguasi & Routing (frasa ambigu)

Berbeda dengan glosarium (kamus arti istilah), pustaka ini menangani **frasa user
yang ambigu / bercabang** — satu kalimat yang bisa berarti beberapa hal — dan
membantu AI memilih makna yang benar saat menganalisis fallback & MKTA.

**Contoh kasus yang ditangani:**
- `lupa password` → DJP Online **atau** Coretax
- `minta efin` → lupa EFIN / aktivasi EFIN / reset EFIN
- `aktivasi` → aktivasi NIK / NPWP / EFIN / akun
- `lapor pajak`, `bayar pajak`, `buat faktur` → alur lama vs Coretax

**Halaman Kelola Disambiguasi** (`http://localhost:8080/disambig`):
- Tabel aturan + pencarian/filter, **tambah/edit/hapus** sendiri.
- **Petunjuk pengisian lengkap** di halaman (format kandidat `label | sistem |
  intent_bot | petunjuk`, kapan pakai aturan waktu, dll).
- Saat pertama dibuka otomatis terisi **7 aturan contoh**.

**Aturan waktu (temporal routing) — khusus DJP:** Coretax mulai berlaku
**Januari 2025**. Untuk frasa umum (pelaporan/pembayaran/login), sistem memutus
**otomatis dari tanggal baris log**: `≥ 2025-01-01` → **Coretax**, `≤ 2024` →
**DJP Online**. Keputusan deterministik (tanpa menebak). Cutoff bisa diatur
per-aturan. Untuk hal lintas-sistem (mis. EFIN), aturan waktu dimatikan dan bot
diarahkan mengajukan **pertanyaan klarifikasi**.

Data disimpan di tabel `disambig` (database yang sama, `PIPELINE_DB_FILE`).
Route API: `GET /api/disambig/list`, `POST /api/disambig/save`,
`POST /api/disambig/delete`. Modul `disambig_db.py` juga menyediakan
`match(query, tanggal)` dan `build_context_text(...)` yang siap dipakai mesin
analisis untuk menyuntik panduan disambiguasi ke prompt.

> **Catatan status:** glosarium & pustaka disambiguasi saat ini sudah lengkap
> sebagai **penyimpanan + UI + fungsi pembangun konteks**. Penyambungan otomatis
> ke prompt mesin analisis (Step 5/7/8) adalah langkah lanjutan yang terpisah.

---

## Peta Intent & Maksud Analis (kunci jawaban analis)

Ini pengetahuan **jenis ketiga** (setelah Glosarium & Disambiguasi). Isinya adalah
**keputusan sengaja analis** dalam memetakan pertanyaan user ke intent Dialogflow —
terutama yang **melawan logika semantik biasa**, sehingga LLM (sebesar apa pun)
mustahil menebaknya. AI memakainya sebagai **“kunci jawaban”** saat menilai fallback
& MKTA agar vonisnya selaras dengan maksud analis, bukan semantik naif.

**Contoh yang ditangani:**
- `lupa email dan no hp` / `email & hp tidak aktif` → intent **Perubahan Data**
  (user tak bisa reset mandiri via chatbot bila kontak sudah tidak aktif) — bukan
  sekadar keluhan login.
- Semua pertanyaan **UMKM** → satu **megaintent** `UMKM` + **bridging intent anakan**
  (`UMKM - Pendaftaran`, `UMKM - PPh Final 0,5%`, dst), karena memecahnya jadi
  banyak intent mirip berisiko menaikkan fallback/MKTA (keterbatasan Dialogflow).
  AI perlu tahu ini agar **tidak menyarankan memecah UMKM**.

**Halaman Peta Intent** (`http://localhost:8080/intentmap`):
- Tabel kebijakan + cari/filter, **tambah/edit/hapus** sendiri.
- Struktur per intent: `mandiri` / `megaintent` (+ daftar bridging) / `bridging_child` (+ induk).
- Kolom **Cakupan**, **Contoh utterance**, **BUKAN ini** (contoh negatif), **Alasan/maksud analis** (wajib), **Batasan Dialogflow**.
- **Prioritas 5 tingkat** (Sangat Tinggi → Tinggi → Sedang → Rendah → Sangat Rendah) untuk menentukan aturan mana menang saat beberapa cocok — dipilih lewat dropdown, tidak perlu angka. Petunjuk lengkapnya ada di panel atas halaman.
- **Petunjuk pengisian lengkap** di halaman + otomatis terisi **5 contoh** saat pertama dibuka.

### 🔗 Penyambungan ketiga pustaka ke mesin analisis (`knowledge_ctx.py`)

Ketiga pustaka (Glosarium, Disambiguasi, Peta Intent) kini **otomatis dipakai**
oleh AI saat menganalisis — bukan sekadar disimpan. Modul `knowledge_ctx.py`
mencari entri yang relevan dengan tiap pertanyaan (lewat `match()` masing-masing
pustaka) lalu menyuntikkan ringkasannya ke *prompt* LLM. Titik yang tersambung:

- **Step 5 — pemilihan intent** (`build_prompt` di `llm_fix_final_combined.py`).
- **Step 8 — putusan MKTA** (`MKTA_VERDICT_PROMPT`).
- **Chat** halaman utama (`/api/chat`).
- **AI tanya-data** (`/api/ask-data`, tahap perangkuman jawaban).

Prinsip: hanya entri yang cocok dengan pertanyaan yang disuntik (ringkas &
terstruktur), diberi label sebagai **acuan internal tim** (bukan perintah dari
user). Bila pustaka kosong / tidak relevan, konteks kosong (aman, tanpa efek).
Temperature tahap analitis dijaga rendah (0–0.2) agar hasil konsisten.
Glosarium juga kini memiliki `match()` + `build_context_text()` seperti dua
pustaka lain.

> **Disambiguasi DJP Online vs Coretax berbasis MASA PAJAK.** Penentu sistem
> adalah masa pajak yang ditanyakan user (mis. "lapor SPT masa Desember 2024"),
> **bukan** tanggal user bertanya (tanggal interaksi). Aturan cutoff (default
> Januari 2025) hanya *disajikan* ke LLM untuk diterapkan berdasarkan masa pajak
> di teks pertanyaan; jika masa pajak tidak disebut, kasus ditandai **AMBIGU**
> dan disarankan klarifikasi. (Fungsi `match(..., tanggal=...)` tetap ada untuk
> pengujian manual, tetapi mesin analisis tidak lagi memakai tanggal interaksi.)

Data disimpan di tabel `intentmap` (database yang sama, `PIPELINE_DB_FILE`).
Route API: `GET /api/intentmap/list`, `POST /api/intentmap/save`,
`POST /api/intentmap/delete`. Modul `intentmap_db.py` menyediakan
`match(query)` (pencocokan toleran variasi kata) dan `build_context_text(...)`
yang siap dipakai mesin analisis untuk menyuntik “kunci jawaban” analis ke prompt.

### Sampai batas mana LLM perlu “diberi tahu”?
Prinsip: **kodifikasikan HANYA keputusan yang (a) tak bisa disimpulkan dari teks +
akal sehat, DAN (b) kalau salah paham mengubah kesimpulan analisis.** Sisanya
biarkan LLM. Setiap aturan yang ditulis adalah beban pemeliharaan — mulai dari
intent kontra-intuitif & bervolume tinggi (Perubahan Data, UMKM), tumbuhkan dari
pola fallback nyata. Model kecil (mis. Qwen) tetap tidak mungkin tahu keputusan
internal ini (bukan soal ukuran model), jadi ketiga pustaka ini justru paling
membantu model kecil — asalkan konteks yang disuntik pendek, terstruktur, dan
hanya yang relevan (hasil `match()`), bukan seluruh isi.

> **Catatan status:** Glosarium, Disambiguasi, dan Peta Intent saat ini sudah
> lengkap sebagai **penyimpanan + UI + fungsi pembangun konteks**. Penyambungan
> otomatis ketiganya ke prompt mesin analisis (Step 5/7/8) adalah langkah lanjutan
> terpisah.

---

## Cara jalan (tanpa Docker) — disarankan

### 1. Prasyarat
- **Python 3.10+** (3.11 disarankan). Cek: `python --version`.
- Tidak perlu PHP.

### 2. Install dependensi
**Linux/macOS:**
```bash
chmod +x install.sh start.sh
./install.sh
```
**Windows:**
```bat
install.bat
```
Skrip membuat virtualenv `.venv`, meng-install **torch (CPU)** + `requirements.txt`.

> **Punya GPU NVIDIA (nanti)?** Ganti torch CPU dengan versi CUDA:
> ```bash
> pip uninstall -y torch
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> ```
> Backend otomatis memakai GPU bila `torch.cuda.is_available()`.

### 3. Konfigurasi
```bash
cp .env.example .env      # Windows: copy .env.example .env
```
Edit `.env`:
- `LLM_PROVIDER=openai` (atau `gemini` / `azure`) + isi API key provider tsb.
- `PIPELINE_API_KEY` boleh dibiarkan default (`sam-n8n-secret`), asal sama di backend & frontend.
- `WEB_PORT=8080` port web UI (ubah bila bentrok).

### 4. (Opsional) Google service-account
Step **1** (tarik log Dialogflow) dan Step **3/13** (tarik intent) butuh akses Google Cloud.
- Taruh file **`service-account.json`** di folder yang sama dengan `web_app.py`, **atau**
- set `PIPELINE_SA_FILE=/path/ke/service-account.json` di `.env`, **atau**
- tempel **Access Token** manual di form step (tanpa file).

File ini **tetap lokal** — tidak diunggah ke mana pun.

### 5. Jalankan
**Linux/macOS:**
```bash
./start.sh
```
**Windows:**
```bat
start.bat
```
Ini menjalankan **backend** (port 8000) lalu **frontend** (port 8080).

### 6. Akses dari PC lain di LAN
1. Cari IP PC ini: `ip a` / `hostname -I` (Linux) atau `ipconfig` (Windows).
2. Buka di PC lain: **`http://<IP-PC-INI>:8080/`**
3. Izinkan port **8080** di firewall:
   - Windows: *Windows Defender Firewall → Inbound Rule → Port 8080 → Allow*.
   - Linux (firewalld): `sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload`
   - Linux (ufw): `sudo ufw allow 8080/tcp`

---

## Cara jalan via Docker (opsional)

Tidak mau install Python/torch manual? Pakai Docker (backend + frontend dalam 1 container):
```bash
cp .env.example .env      # isi API key
# (opsional) taruh service-account.json di folder ini
docker compose up --build
```
Akses: `http://<IP-PC-HOST>:8080/`.

> Catatan: image mengunduh torch CPU + model HuggingFace saat pertama kali (butuh internet & disk cukup). Cache model disimpan di volume `hf_cache`.

---

## Alur Step (identik dengan versi PHP)

**Dialogflow:** 1) Tarik log → 2) JSON→XLSX → 3) Training & Intent → 4) Analisis rekomendasi → 5) Judgement LLM → 6) Cross-check manual → 7) Analisis MKTA → 8) Putusan LLM MKTA → 9) Analisis manual MKTA → 10) Laporan LM & Pembaruan → 11) Pembaruan intent (usersays).

**Avaya:** 12) Upload JSON → 13) Tarik intent → 14) Analisis → 15) Dashboard → 16) Ekspor Excel.

Setiap step berdiri sendiri; hasil step yang sukses tersimpan di `_runs/<run>/` dan bisa dipakai step berikutnya tanpa upload ulang — **sama persis** seperti sebelumnya.

---

## Variabel lingkungan penting

| Var | Default | Fungsi |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` / `gemini` / `azure` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Kredensial OpenAI |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-1.5-flash` | Kredensial Gemini |
| `AZURE_OPENAI_*` | — | Kredensial Azure OpenAI |
| `PIPELINE_API_KEY` | `sam-n8n-secret` | Kunci internal frontend↔backend |
| `PIPELINE_PORT` / `PIPELINE_API_HOST` | `8000` / `127.0.0.1` | Bind backend |
| `PIPELINE_API_BASE` | `http://127.0.0.1:8000` | Alamat backend yg dipanggil frontend |
| `PIPELINE_FORCE_LOCAL` | `1` | Selalu pakai backend lokal (abaikan field URL di form) |
| `WEB_HOST` / `WEB_PORT` | `0.0.0.0` / `8080` | Bind web UI |
| `PIPELINE_SA_FILE` | `./service-account.json` | Path service account Google |
| `PIPELINE_PROJECT_ID` | `avaya-djp-klipbot-prod` | Project Dialogflow |
| `PIPELINE_RUNS_DIR` | `./_runs` | Folder hasil kerja |

---

## Troubleshooting

- **Web UI kebuka tapi step gagal “Server error / connection refused”** → backend (port 8000) belum siap. Lihat jendela/terminal backend; tunggu model selesai load.
- **Step 1/3 gagal “service-account.json tidak ditemukan”** → taruh file SA atau tempel Access Token di form.
- **`google-auth` belum terpasang** → `pip install google-auth` (sudah masuk `requirements.txt`).
- **PC lain tidak bisa buka** → cek firewall port 8080 & pastikan `WEB_HOST=0.0.0.0`.
- **Torch lambat / ingin GPU** → install torch versi CUDA (lihat langkah 2).
