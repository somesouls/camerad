# CHANGES v21 — Fase 5: Indeks Q&A historis (Q2Q) + tautan rujukan peraturan

## Ringkasan

Mengimplementasikan usulan pengguna (19 Agu 2026): data Sosmed & Livechat AWE
sudah berbentuk pertanyaan-jawaban asli pengguna berbulan-bulan, bahasanya
sangat mirip pertanyaan baru. Maka mesin mencari berdasarkan **kemiripan
PERTANYAAN** (question-to-question / Q2Q), bukan kemiripan jawaban/dokumen;
lalu rujukan peraturan yang muncul di jawaban historis dideteksi otomatis
(berbagai format: "PMK 10 2025", "PMK Nomor 10 Tahun 2025", "PMK No 10 TH
2025", "PER-23/PJ/2025", dst.) dan ditautkan ke basis peraturan yang rapi.

## Perubahan

### 1. `regref.py` (baru) — detektor + resolver rujukan peraturan
- Regex multi-format: jenis (UU/PERPU/PP/PERPRES/PMK/KMK/PER/KEP/SE) + nomor
  utama + sub-kode opsional (/PJ, /PMK.03) + tahun ("Tahun"/"TH"/garis miring).
- Resolusi ke `peraturan_unit` dengan peta tercache: kecocokan (jenis+tahun+
  nomor utama) -> sub-kode -> prioritas status 'berlaku' lalu terbaru.
  Tak cocok = tak ditebak (match=None).

### 2. `qa_index_db.py` (baru) — penyimpanan & pembangunan indeks Q&A
- Tabel `qa_unit` (question/answer/topik/url/reg_json) + `qa_vec` (BLOB float32,
  cosine numpy — pola yang sama seperti peraturan_vec).
- Kolektor: Sosmed memakai `faq_pairs(only_answered=True)`; AWE dari
  `awe_conversations` dengan bot-filter `awe_botfilter_patch` dipakai ulang.
- **PII di-mask (`pii_mask`) sebelum disimpan.** Dedup per pertanyaan
  ternormalisasi. Embed memakai model bge-m3 yang sama; **resume** per dimensi.
- `search()`: Q2Q cosine dengan ambang `RAG_QA_MIN_COS` (default 0.50).

### 3. `rag_qa_patch.py` (baru) — integrasi ke jalur retrieval
- Membungkus `_ctx_awe`/`_ctx_sosmed` versi v16: jalur leksikal tetap, hasil
  Q2Q DITAMBAHKAN sebagai blok "Pertanyaan serupa dari riwayat" + blok
  "Peraturan tertaut otomatis" (isi pasal status berlaku; bila rujukan historis
  kedaluwarsa -> catatan status, bukan kutipan lama).
- Fail-soft penuh: tanpa `qa.db`, perilaku persis v16. Kill-switch `RAG_QA_PATCH=0`.

### 4. `phase5_qa_build.py` (baru) — pembangun indeks (CLI)
```
python phase5_qa_build.py            # bangun/isi (idempoten + resume)
python phase5_qa_build.py --stats    # ringkasan indeks
```

### 5. `web_app.py` — impor `rag_qa_patch` (setelah `rag_sources_patch`).
### 6. `.env.example` — blok Fase 5 (`RAG_QA_PATCH`, `RAG_QA_MIN_COS`,
   `RAG_QA_TOPK`, `RAG_QA_LINK_REG`, `PIPELINE_QA_DB_FILE`).

## Urutan penerapan

1. `git pull origin main`
2. `python phase5_qa_build.py` (beberapa menit; embed hanya pasangan baru)
3. Restart `web_app.py` — cari log `[rag_qa_patch] Q2Q aktif (...)`
4. Uji `/rag-lab` dengan pertanyaan informal yang mirip riwayat pengguna;
   pastikan muncul blok "Pertanyaan serupa dari riwayat" + tautan peraturan.
5. Ukur dampak: `python phase4_eval.py --baseline-check golden_baseline.json`
   (tidak boleh turun); pertimbangkan menambah query golden dari kasus nyata.
6. Jalankan ulang `phase5_qa_build.py` berkala (mis. mingguan) — resume otomatis.

## Catatan desain (jawaban atas penilaian risiko)

- Jawaban historis TIDAK pernah disalin mentah sebagai jawaban akhir — ia
  jembatan konteks; guardrail grounding (v13/v18) tetap berjalan di atasnya.
- Rujukan kedaluwarsa tidak dikutip lamanya; diberi catatan status dan jalur
  successor-tracing (Fase 2) mencari penggantinya.
- Ambiguitas rujukan: resolver memilih 'berlaku'+terbaru atau tidak menebak.

## Rollback

`RAG_QA_PATCH=0` di `.env` lalu restart. Menghapus `qa.db` meniadakan indeks;
jalur v16 tetap utuh.
