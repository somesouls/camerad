# Panduan: Menjalankan Chatbot Lokal & Menghubungkan ke Webhook Dialogflow ES

Dokumen ini menjelaskan tiga hal:

1. Cara menjalankan server chatbot Camerad di PC lokal.
2. Cara menguji chatbot dalam 3 mode (Tanpa LLM / Dengan LLM / Full LLM) lewat tombol switch.
3. Cara menghubungkan server lokal ke webhook Dialogflow ES (yang tidak bisa mengakses `localhost`), plus cara mengakses chatbot dari PC lain.

---

## 1. Menjalankan server chatbot di PC lokal

Aplikasi ini adalah server **FastAPI (Python)** yang me-render halaman dan menjalankan mesin RAG di dalam proses yang sama. Ada backend LLM terpisah pada port `8000` dan web app pada port `8080`.

### Opsi A — langsung dengan Python

```bash
# 1. Siapkan environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Salin & sesuaikan variabel lingkungan
cp .env.example .env             # isi OPENAI_API_KEY / kredensial LLM Anda

# 3. Jalankan web app (port 8080)
python web_app.py
# atau setara:
# uvicorn web_app:app --host 0.0.0.0 --port 8080
```

Saat start, aplikasi mencetak alamat yang bisa dipakai dari PC lain di LAN, mis.:

```
Dari PC lain (LAN): http://<IP-PC-INI>:8080/
```

### Opsi B — Docker

```bash
docker compose up --build
```

`docker-compose` memetakan `8080:8080`. Setelah jalan, buka `http://localhost:8080/`.

> Catatan: `WEB_HOST=0.0.0.0` (default di `.env.example`) penting agar server bisa diakses dari luar `localhost`. Jika diset `127.0.0.1`, server hanya bisa diakses dari PC itu sendiri.

---

## 2. Menguji 3 mode chatbot (tombol switch)

Buka halaman **Webhook Chatbot** di Studio: `http://localhost:8080/df-webhook`.

Di kartu **Pengaturan** kini ada dropdown **Mode mesin** dengan 4 pilihan:

| Pilihan | Nilai | Perilaku | Kecepatan |
|---|---|---|---|
| Auto | `""` | Ikut setelan server (profil `chatbot` = cepat bawaan) | — |
| 1 · Tanpa LLM | `tanpa_llm` | **Tidak memanggil LLM generatif sama sekali.** Jawaban diambil dari intent terverifikasi (jawaban cuplikan) lalu cuplikan teratas hasil retrieval; jika kosong → kalimat fallback. Retrieval boleh tetap pakai embedding/reranker (bukan LLM generatif). | Paling cepat |
| 2 · Dengan LLM (hemat) | `llm` | Pakai LLM untuk sintesis jawaban, tetapi **tanpa** loop verifikasi & **tanpa** AI-rewrite kueri. Fast-path intent tetap aktif bila diizinkan. | Sedang |
| 3 · Full LLM | `full` | Pipeline penuh: AI-rewrite kueri + loop verifikasi kecukupan konteks + eskalasi peraturan + sintesis grounded. | Paling lengkap (paling lambat) |

### Cara memakai

1. Pilih **Profil mesin RAG** (mis. `chatbot`).
2. Pilih **Mode mesin** yang ingin diuji.
3. Klik **Simpan Pengaturan**. Mode disimpan pada profil tersebut dan berlaku di semua kanal (chatbot web `/rag` maupun webhook Dialogflow).
4. Ketik pertanyaan di kotak **Uji Fast-Path** → **Jalankan Uji**. Hasil menampilkan jawaban + **waktu proses (`elapsed_s`)** terhadap deadline, sehingga Anda bisa membandingkan kecepatan tiap mode.
5. Ganti mode, simpan lagi, uji lagi — bandingkan `elapsed_s` dan kualitas jawaban.

> Tips: untuk webhook Dialogflow (deadline ~5 dtk), mode **Tanpa LLM** atau **Dengan LLM (hemat)** paling aman. Mode **Full LLM** cocok untuk kanal tanpa deadline ketat (mis. “Agent Kring Pajak” di halaman agent).

---

## 3. Menghubungkan server lokal ke webhook Dialogflow ES

**Masalah:** Dialogflow ES berada di cloud Google dan **tidak bisa** menjangkau `http://localhost:8080` di PC Anda. Webhook Dialogflow **wajib HTTPS publik**. Solusinya: buat **tunnel** yang mengekspos port 8080 lokal ke sebuah URL HTTPS publik.

### 3a. ngrok (paling cepat & direkomendasikan)

```bash
# Instal ngrok, login sekali dengan authtoken dari dashboard ngrok
ngrok config add-authtoken <TOKEN_ANDA>

# Ekspos port 8080
ngrok http 8080
```

ngrok menampilkan URL publik, mis. `https://ab12-34-56.ngrok-free.app`.

**Penting:** buka halaman webhook **melalui URL tunnel**, bukan localhost:

```
https://ab12-34-56.ngrok-free.app/df-webhook
```

Dengan begitu, kolom **Webhook URL** di halaman itu otomatis terisi domain tunnel (aplikasi menyusun URL dari alamat yang Anda pakai), sehingga siap ditempel ke Dialogflow tanpa diedit manual.

### 3b. Cloudflare Tunnel (alternatif gratis, domain stabil)

```bash
cloudflared tunnel --url http://localhost:8080
```

Akan muncul URL `https://<acak>.trycloudflare.com`. Untuk domain tetap, buat named tunnel dan pasang di domain Anda.

### 3c. VPS / port-forward (untuk produksi)

Untuk pemakaian permanen, deploy aplikasi ke VPS (mis. dengan Docker) di belakang reverse proxy (Nginx/Caddy) yang memberi HTTPS. Ini lebih stabil daripada tunnel dari PC pribadi yang bisa mati saat PC dimatikan.

### 3d. Pasang di Dialogflow ES

1. Dialogflow ES → **Fulfillment** → aktifkan **Webhook**.
2. **URL** = `https://<domain-tunnel>/api/df/webhook` (nilai yang tampil di kartu **Endpoint Fulfillment**).
3. Tambahkan header **`X-Camerad-Token`** = token yang tampil di halaman (atau kirim lewat query `?token=...`).
4. **Simpan**. Lalu pada intent yang ingin dijawab RAG (mis. **Default Fallback Intent**), centang **Enable webhook call for this intent**.
5. Uji di **simulator** Dialogflow.

> **Deadline:** Dialogflow ES memutus webhook pada ~5 detik. Pertahankan **Deadline guard** ≈ 4500 ms. Bila jawaban belum siap, pengguna menerima **kalimat fallback** yang sopan (HTTP 200) alih-alih error. Karena itu mode **Tanpa LLM / Dengan LLM (hemat)** direkomendasikan untuk kanal ini.

---

## 4. Mengakses chatbot dari PC lain

### 4a. Dalam satu jaringan (LAN) — paling sederhana

1. Pastikan server jalan dengan `WEB_HOST=0.0.0.0` (default).
2. Cari IP PC server: `ipconfig` (Windows) atau `ifconfig` / `ip a` (Linux/Mac), mis. `192.168.1.10`.
3. Dari PC lain di jaringan yang sama, buka: `http://192.168.1.10:8080/`.
4. Izinkan port **8080** di firewall PC server bila diblokir.
5. Anda tetap perlu **login** (ada middleware autentikasi); halaman `/api/df/webhook` yang publik hanya untuk fulfillment Dialogflow (dilindungi token).

### 4b. Dari internet (PC di luar jaringan)

Gunakan **URL tunnel** (ngrok/cloudflared) yang sama dari bagian 3, mis. buka `https://<domain-tunnel>/` di PC mana pun. Ingat halaman Studio tetap butuh login.

### 4c. Untuk produksi

Deploy ke **VPS** dengan HTTPS (reverse proxy). Ini memberi URL tetap dan uptime yang jauh lebih baik daripada PC pribadi.

---

## 5. Bagaimana dengan Netlify?

**Netlify tidak cocok** untuk meng-host aplikasi ini sebagaimana adanya, karena:

- Aplikasi ini **server-rendered FastAPI (Python)** dan menjalankan mesin RAG + akses basis data di dalam prosesnya. Netlify hosting-nya berbasis **situs statis + serverless functions singkat**, bukan server Python yang berjalan terus-menerus dengan model/DB lokal.
- Berkas DB (`rag.db`, `peraturan.db`, `df_webhook.db`, dst.) dan proses backend LLM (port 8000) tidak akan tersedia di lingkungan Netlify.

**Yang mungkin dilakukan** (opsional, di luar cakupan sekarang): pisahkan menjadi

- **Frontend statis** (HTML/JS) di Netlify yang hanya memanggil **API backend** Anda; **dan**
- **Backend Python** tetap di VPS/tunnel.

Itu perlu tambahan: **CORS** (aplikasi saat ini **belum** punya middleware CORS) dan mekanisme autentikasi lintas domain. Jadi untuk sekarang, **gunakan LAN / tunnel / VPS**, bukan Netlify.

---

## 6. Checklist ringkas

- [ ] `pip install -r requirements.txt` & isi `.env`.
- [ ] Jalankan `python web_app.py` (atau `docker compose up --build`).
- [ ] Buka `http://localhost:8080/df-webhook`, pilih profil + **Mode mesin**, **Simpan**, uji lewat **Uji Fast-Path**.
- [ ] Jalankan `ngrok http 8080`, buka `https://<tunnel>/df-webhook`.
- [ ] Salin **Webhook URL** + **token** ke Dialogflow ES (header `X-Camerad-Token`).
- [ ] Aktifkan webhook pada intent, uji di simulator.
- [ ] Akses dari PC lain: LAN `http://<IP>:8080/` atau URL tunnel; login diperlukan.
