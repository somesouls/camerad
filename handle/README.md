# OtoPost Clip Server

Server kecil yang menangani bagian yang TIDAK bisa diandalkan di HP:

- **Transkrip ASLI** video YouTube (subtitle manual/otomatis) via `yt-dlp`
- **Download video HD** via `yt-dlp` (gabung video+audio dengan `ffmpeg`)
- **Potong per-segmen** + **reframe 9:16 ke wajah** via `ffmpeg` + OpenCV

HP cukup memanggil server ini lalu mengunduh hasil clip yang sudah jadi.

---

## Endpoint

### `GET /`
Health check. Balasan: `{ "ok": true }`.

### `POST /transcript`
Body:
```json
{ "url": "https://youtu.be/xxxx", "langs": ["id", "en"] }
```
Balasan (ringkas):
```json
{
  "videoId": "xxxx", "title": "...", "channelName": "...", "durationSec": 1234,
  "hasTranscript": true, "transcriptText": "...",
  "segments": [ { "startSec": 12.3, "text": "..." } ]
}
```

### `POST /clips`
Body:
```json
{
  "url": "https://youtu.be/xxxx",
  "aspectRatio": "9:16",
  "reframe": true,
  "maxHeight": 1080,
  "segments": [ { "startSec": 30, "endSec": 75, "title": "Hook 1" } ]
}
```
Balasan:
```json
{ "job": "ab12", "clips": [ { "index": 1, "title": "Hook 1", "reframed": true, "downloadUrl": "https://SERVER/files/ab12/clip_1.mp4" } ] }
```
App tinggal unduh tiap `downloadUrl`.

---

## Environment variables

| Env | Wajib | Keterangan |
|-----|-------|-----------|
| `PUBLIC_BASE_URL` | disarankan | URL publik server, mis. `https://otopost.up.railway.app`. Dipakai untuk membentuk `downloadUrl`. |
| `PORT` | otomatis | Diisi otomatis oleh Railway/Render/Cloud Run. |
| `CLIP_SERVER_TOKEN` | opsional | Kalau diisi, semua request harus kirim header `Authorization: Bearer <token>`. |
| `YTDLP_COOKIES` | opsional | Path file cookies Netscape kalau IP server diblok YouTube ("Sign in to confirm..."). |

---

## Deploy (pilih salah satu)

### A. Railway (paling gampang)
1. Buka railway.app -> New Project -> Deploy from GitHub -> pilih repo `otopost`.
2. Set **Root Directory** ke `server`. Railway auto-detect Dockerfile.
3. Tambah variable `PUBLIC_BASE_URL` = domain yang diberikan Railway (isi setelah domain muncul, lalu redeploy).
4. Deploy. Tes buka `https://<domain>/` -> harus `{"ok":true}`.

### B. Render
1. render.com -> New -> Web Service -> connect repo.
2. Root Directory `server`, Runtime **Docker**.
3. Tambah env `PUBLIC_BASE_URL` = URL Render-mu. Deploy.

### C. Google Cloud Run
```bash
cd server
gcloud run deploy otopost-clip --source . --region asia-southeast2 --allow-unauthenticated --memory 2Gi --cpu 2 --timeout 900
```
Lalu set `PUBLIC_BASE_URL` ke URL Cloud Run (redeploy sekali).

### D. VPS (Docker)
```bash
cd server
docker build -t otopost-clip .
docker run -d -p 8080:8080 \
  -e PUBLIC_BASE_URL=http://IP_VPS:8080 \
  -v /opt/otopost-clips:/data/clips \
  --name otopost-clip otopost-clip
```

> Saran: pakai memory >= 2 GB. Download+encode butuh CPU; segmen panjang makan waktu.

---

## Tes cepat (curl)
```bash
curl -X POST https://<domain>/transcript \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://youtu.be/VIDEO_ID"}'
```

---

## Catatan jujur
- Kalau server jalan di IP datacenter, YouTube kadang minta verifikasi bot. Solusi: set `YTDLP_COOKIES` (export cookies dari browser) atau jalankan di IP residensial.
- `yt-dlp` perlu di-update berkala (`pip install -U yt-dlp` / rebuild image) karena YouTube sering berubah.
- Reframe saat ini = crop ke 1 wajah terdeteksi (sesuai kesepakatan). Active-speaker (fokus yang sedang bicara) menyusul.

---

## Integrasi ke app
Di app OtoPost, isi **Clip Server URL** di halaman Settings dengan `PUBLIC_BASE_URL` (dan token bila dipakai). Setelah itu alur Clip Podcast otomatis memakai server ini untuk transkrip, download, dan potong.
