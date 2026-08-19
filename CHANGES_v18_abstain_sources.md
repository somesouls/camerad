# CHANGES v18 — Hotfix: abstain tanpa daftar sumber

## Latar

Ditemukan dari uji live (19 Agu 2026): saat mesin menjawab dengan kalimat
fallback (abstain — benar, karena topik memang tidak ada di basis data), UI
tetap menampilkan daftar **"Sumber Rujukan"** berisi hasil retrieval yang
lemah (posting sosmed, percakapan AWE, pasal yang kebetulan menyebut istilah
serupa). Ini menyesatkan petugas: seolah sumber-sumber itu mendukung jawaban.

## Perubahan

### `rag_grounding_patch.py` — guardrail 0 (baru, berjalan lebih dulu)
- Bila jawaban akhir PERSIS kalimat fallback profil (toleran spasi/awalan),
  `sources` dikosongkan dan `guardrail` diberi penanda `{abstain: True, alasan}`.
- Tidak memengaruhi jalur lain: guardrail tautan (URL tidak resmi/pemendek)
  dan guardrail anti-karang-pasal tetap bekerja seperti semula.
- Kill-switch: `RAG_GUARD_FALLBACK_SRC=0`.

## Penerapan

`git pull origin main` lalu restart `web_app.py`. Tidak ada migrasi.

## Catatan terkait

Hit retrieval yang lemah pada query abstain akan semakin jarang setelah ambang
`RAG_MIN_COS` dikalibrasi lewat `/rag-eval` (langkah Fase 0 yang tersisa).
