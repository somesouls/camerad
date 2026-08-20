# CHANGES v31 — Perbaikan "looping" /livechat (poll tanpa Dialogflow)

## Gejala (20 Agu 2026)

Log server membanjir pasangan tak berujung:
```
POST /api/chat/detect  (browser, 82.158.131.150)
POST /api/df/webhook   (Google Dialogflow, 66.249.83.x)
POST /api/chat/detect
POST /api/df/webhook
... terus-menerus, bahkan SETELAH "[Opsi B] ... jawaban siap" ...
```
User: "malah looping ... gak karu-karuan".

## Akar penyebab

Mekanisme Opsi B (echo/poll) membuat frontend /livechat melakukan polling tiap
1,5 dtk untuk menanyakan "jawaban sudah siap?". Masalahnya, **setiap poll ikut
memanggil Dialogflow `detect_intent`** (mengirim teks sentinel
`__CAMERAD_POLL__`). Padahal:

- Tiap `detect_intent` pada intent ber-fulfillment memicu **satu callback
  `/api/df/webhook`** → dengan POLL_MAX=80 & interval 1,5 dtk, satu pertanyaan
  bisa menghasilkan puluhan pasangan detect+webhook (badai yang terlihat sebagai
  "looping").
- Jawaban RAG SEBENARNYA sudah dihitung sekali oleh webhook giliran-pertanyaan
  dan disimpan di job-store in-process (`_JOBS`). Polling seharusnya cukup
  MEMBACA job-store itu — bukan menembak Dialogflow lagi.
- Bonus buruk: tiap poll masuk sebagai giliran user `__CAMERAD_POLL__` di
  transkrip Avaya (komentar di `df_webhook_routes` sendiri sudah menandai ini).

## Perbaikan (backend saja — `chat_frontend_routes.py`)

`POST /api/chat/detect` untuk giliran **POLL** (`text == SENTINEL_POLL`) kini
membaca `dfw.ambil_job(session_id)` LANGSUNG (proses sama) dan mengembalikan
`ready/pending` — **tanpa** memanggil Dialogflow sama sekali.

Giliran **PERTANYAAN** (bukan sentinel) TETAP lewat Dialogflow, karena di situlah
webhook (a) memulai komputasi RAG (Opsi B), (b) merekam percakapan (mis. Avaya),
dan (c) handoff ke agen terdeteksi dari intent connector. Saat giliran pertanyaan
kembali, job sudah ADA di store (webhook berjalan sinkron dalam rentang deadline
sebelum Dialogflow membalas), jadi polling berikutnya pasti menemukannya.

### Dampak

| | Sebelum | Sesudah |
| --- | --- | --- |
| Panggilan Dialogflow / pertanyaan | 1 + (s.d. 80 poll) | tepat 1 |
| Callback `/api/df/webhook` / pertanyaan | s.d. 81 | tepat 1 |
| Deteksi "selesai" | via round-trip DF (rentan lomba/latensi) | baca `_JOBS` in-process (deterministik) |
| Transkrip Avaya | tercemar `__CAMERAD_POLL__` tiap poll | bersih (hanya giliran nyata) |
| Frontend `chat.html` | — | tidak berubah |

Mekanisme Opsi B lain tidak berubah: fast-path deadline giliran-1, job-store,
durasi_backend, handoff, jalur bahasa EN↔ID. Cabang sentinel di
`df_webhook_routes` dibiarkan (aman) untuk klien Dialogflow lain.

## Penerapan

`git pull origin main` → restart `web_app.py`. Tanpa migrasi, tanpa perubahan
frontend/env. URL tetap `/livechat`.