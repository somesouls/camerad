# CHANGES v30 — Guardrail anti-karang-pasal: bukti dukungan = seluruh konteks

## Gejala (19 Agu 2026)

Uji "gimana cara aktivasi EFIN?" (profil chatbot): konteks retrieval **jelas
memuat jawaban** (3 pasangan sosmed + 3 pasangan Q2Q, kemiripan s.d. 0.66,
semua soal aktivasi EFIN), namun jawaban akhir = kalimat fallback persis,
dengan badge tetap `grounded` dan tanpa daftar sumber.

## Akar penyebab

**Guardrail 2 (anti-karang-pasal) di `rag_grounding_patch.py` menilai dukungan
rujukan HANYA dari judul/ref/url sumber** — sejak v22 pembungkus tidak menerima
konteks mentah (diakui di komentar kodenya sendiri). Dampaknya:

1. Jawaban EFIN yang sah wajar mengutip **Pasal 6a PER-06/PJ/2019** — nomor itu
   ada di *isi* jawaban sosmed/Q2Q, tapi TIDAK ada di judul/ref sumber
   (judulnya "Aktivasi EFIN"/"Data Sosmed").
2. Guardrail menyatakan rujukan "tak terdukung" → **seluruh jawaban diganti
   fallback**, `grounded` tetap True (badge menyesatkan), sumber dikosongkan
   (guardrail v18).

Ini juga penjelasan yang lebih mungkin untuk abstain-abstain semu sebelumnya
(mis. SKB, 19 Agu 2026) — rujukan sah yang hidup di *badan* konteks selalu
berisiko dinyatakan karangan.

## Perbaikan

1. **`rag_engine.py`** — meneruskan konteks mentah hasil retrieval ke guardrail
   lewat kunci privat `_konteks_internal` pada jalur sintesis, jalur cepat
   intent, dan mode tanpa LLM.
2. **`rag_grounding_patch.py`** — kunci privat **selalu di-pop secepatnya**
   (tidak pernah ikut terkirim ke klien); Guardrail 2 kini menilai dukungan
   terhadap konteks mentah + metadata sumber, dengan pencocokan
   **ternormalisasi** (tanda baca dibuang: "PER-06/PJ/2019" → "per06pj2019"
   memuat "06pj2019"). Rujukan yang benar-benar dikarang tetap tertahan.
3. **`rag_config_db.py`** — migrasi lunak: bila prompt profil **chatbot**
   ternyata PERSIS prompt bawaan *agent* (salah-isi bawaan lama — teramati di
   kasus yang sama), dikembalikan ke prompt bawaan chatbot (anti over-abstain).
   Prompt kustom admin tidak disentuh.

## Verifikasi

`git pull origin main` → restart `web_app.py` → uji ulang "gimana cara
aktivasi EFIN?": jawaban seharusnya tampil (permohonan aktivasi EFIN ke
KPP/KP2KP sesuai Pasal 6a PER-06/PJ/2019, dst.). Kill-switch tetap tersedia:
`RAG_GUARD_PASAL=0`.