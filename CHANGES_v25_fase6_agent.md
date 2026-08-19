# CHANGES v25 — Fase 6: Penyesuaian RAG untuk agen (ekspansi utas X + drill-down ketentuan pelaksana)

## Latar

Usulan pengguna (19 Agu 2026) setelah RAG Agent dipakai live:

1. Jawaban X/Twitter historis itu berbentuk UTAS bercabang — penggalian
   tanya-jawab lanjutannya sebaiknya ikut tersaji ("digali sampai bawah"),
   bukan hanya pasangan pertamanya.
2. Aturan tingkat atas (UU/PP) sifatnya umum; detail teknisnya ada di
   PMK/PER/SE di bawahnya — mesin sebaiknya pintar mencari "aturan di
   bawahnya" secara otomatis.

## Perubahan

### 1. `qa_index_db.py` — kolom `conv_id` + filter isi
- Skema `qa_unit` bertambah `conv_id` (id percakapan/utas asal; migrasi ringan
  `ALTER TABLE` untuk DB lama — jalankan ulang `phase5_qa_build.py`, metadata
  terisi ulang tanpa meng-embed ulang vektor).
- Pasangan nyaris-tanpa-isi (< 3 token bermakna pasca-normalisasi, mis. sapaan
  saja) dibuang saat build.
- Fungsi baru `siblings(conv_id, exclude_id, ...)`: pasangan lain dalam utas
  yang sama, urut kronologis mendekati (external_id snowflake X).

### 2. `rag_qa_patch.py` — ekspansi utas pada hasil Q2Q Sosmed
- Blok "Pertanyaan serupa dari riwayat" kini disusul bagian **"Penggalian
  dalam utas yang sama"** (tanya lanjutan + jawaban petugas, maks 4).
- Kill-switch: `RAG_QA_THREAD=0`.

### 3. `rag_drilldown_patch.py` (baru) — drill-down ketentuan pelaksana
- Bila kandidat teratas peraturan berlevel UU/PERPU/PERPRES/PP, mesin mencari
  dokumen berlevel lebih rendah yang **terverifikasi merujuk nomor induknya**:
  prefilter SQL ringan -> verifikasi ketat `regref.detect()` (multi-format:
  "PP 111 Tahun 2000", "PP Nomor 111/2000", dst.) -> syarat level lebih rendah.
- Sengaja langsung via SQL (bukan `pdb.search`) agar kebal gerbang cosine
  `RAG_MIN_COS` — dokumen pelaksana memang tidak selalu mirip semantik dengan
  query, tetapi merujuk induknya.
- Hasil disertakan sebagai blok **"Ketentuan pelaksana"** + sumber berlabel.
- Env: `RAG_DRILLDOWN=0` (matikan), `RAG_DRILLDOWN_MAX=2`.

### 4. `web_app.py` — impor `rag_drilldown_patch` (setelah `rag_domain_patch`).
### 5. `.env.example` — blok Fase 6 + opsi `HF_HUB_OFFLINE`.

## Urutan penerapan

1. `git pull origin main`
2. `python phase5_qa_build.py` (mengisi `conv_id`; resume — tanpa embed ulang)
3. Restart `web_app.py` — cari log `[rag_drilldown_patch] drill-down ... aktif`
   dan `[rag_qa_patch] Q2Q aktif (..., utas=True; ...)`
4. Uji di `/rag-agent`: (a) pertanyaan yang cocok dengan utas X berlanjut —
   blok penggalian muncul; (b) pertanyaan yang top-1-nya UU/PP — blok
   "Ketentuan pelaksana" muncul bila ada PMK/PER perujuk di basis data.
5. Gerbang: `python phase4_eval.py --baseline-check golden_baseline.json`.

## Rollback

`RAG_QA_THREAD=0` dan/atau `RAG_DRILLDOWN=0` di `.env` + restart.
