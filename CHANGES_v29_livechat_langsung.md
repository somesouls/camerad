# CHANGES v29 — Rombak halaman /livechat: chat langsung tanpa form ber-step

## Permintaan pengguna (19 Agu 2026)

"rombak halaman /livechat (chat.html), agar tidak perlu pake form ber-step —
langsung pake kolom chat. Pilihan bahasa di bagian atas. Tidak pakai input nama
dll, termasuk klik modal. User bisa langsung chat, temporary — tidak usah
disimpan chatnya."

## Yang berubah (murni frontend — templates/chat.html ditulis ulang)

| Sebelum | Sesudah |
| --- | --- |
| Tombol melayang → klik untuk membuka panel popup | Halaman chat langsung terbuka penuh (kolom maks 520px) |
| Layar 1: pilih bahasa (kartu + tombol Selanjutnya) | Pilihan bahasa 🇮🇩/🇬🇧 pindah ke **header atas**, bisa diganti kapan pun |
| Layar 2: pilih identitas (NPWP/Non-NPWP) + form nama/email/telp/topik | **Dihapus total** — backend memang tidak pernah membaca field ini |
| Sapaan menyebut nama dari form | Sapaan umum langsung tampil saat halaman dibuka |
| — | Sesi tetap **temporary**: ID acak per muat halaman, tanpa penyimpanan riwayat di klien (refresh = sesi baru) |

## Verifikasi kontrak backend (chat_frontend_routes.py)

- `POST /api/chat/detect` hanya membaca `text`, `session_id`, `lang` —
  `nama`/`topik`/email/telp tidak dibaca sama sekali, jadi **tidak ada
  perubahan backend**.
- `lang` kini dikirim pada **setiap** pesan (bukan hanya giliran pertama), jadi
  ganti bahasa di tengah percakapan langsung berlaku (backend menyimpan bahasa
  per `session_id` di memori).
- Mekanisme Opsi B dipertahankan persis: fast-path + echo/poll
  (`SENTINEL_POLL`, interval 1,5 dtk). `POLL_MAX` dinaikkan 40 → 80 (±120 dtk)
  mengingat jawaban RAG bisa lambat lewat domain publik.
- **Handoff ke agen langsung tetap berfungsi** (intent connector Dialogflow →
  mode agen + pesan antrean), termasuk placeholder/subjudul dua bahasa.

## Penerapan

`git pull origin main` → restart `web_app.py`. Tanpa migrasi, tanpa perubahan
env. URL tetap `/livechat`.