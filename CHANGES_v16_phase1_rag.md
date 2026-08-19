# CHANGES v16 — Fase 1: Pemerataan kualitas retrieval (normalisasi + bobot + rerank)

## Ringkasan

Menutup gap P1 dari audit RAG Profil Agent: jalur PERATURAN sudah full-stack
(perluasan kamus + AI rewrite, hybrid FTS5+vektor, rerank cross-encoder),
sedangkan sumber lain tertinggal dan lexical search belum menormalisasi Bahasa
Indonesia. Fase 1 meratakannya TANPA mengubah arsitektur.

## Perubahan

### 1. `text_norm.py` (baru) — normalisasi Bahasa Indonesia
- lowercase + buang diakritik + non-alfanumerik -> spasi.
- Buang stopword Bahasa Indonesia.
- Stemming Sastrawi per token (opsional; env `RAG_NORM_STEM=1` default aktif;
  fail-soft bila paket belum terpasang -> normalisasi dasar tetap jalan).
- Efek: "menyerahkan" ≈ "penyerahan" ≈ "diserahkan" kini berpadu.

### 2. `peraturan_db.py` — FTS v2 (ternormalisasi + bm25 berbobot)
- Tabel meta `peraturan_meta` (key/value) sebagai penanda versi indeks.
- `rebuild_fts_norm()`: bangun ulang `peraturan_fts` menjadi kolom
  `(id, judul, hierarchy, isi)` berisi teks TERNORMALISASI. Idempoten.
- Setelah migrasi (meta `fts_version='2'`), `_fts_ids` otomatis memakai query
  token ternormalisasi + **bm25 berbobot**: `bm25(peraturan_fts, 0.0, 10.0, 4.0, 1.0)`
  — judul 10×, hierarchy 4×, isi 1×.
- `_sync_fts` menulis konten ternormalisasi begitu v2 aktif; sebelum migrasi,
  format lama tetap dipakai (transisi aman).
- `fts_info()` untuk diagnosis versi/cakupan indeks.
- **Sebelum migrasi dijalankan, perilaku persis seperti v14/v15.**

### 3. `sop_db.py` — pola yang sama untuk SOP
- `sop_meta`, `rebuild_fts_norm()`, `fts_info()`; bobot `bm25(sop_fts, 0.0, 10.0, 4.0, 1.0)`
  — judul 10×, bagian 4×, isi 1×.

### 4. `rag_sources_patch.py` (baru) — pemerataan SOP / AWE / Sosmed
- **SOP**: pembungkus `sop_db.search` (pola rag_rerank_patch): perluasan kamus
  + AI rewrite (`rag_rewrite.untuk_retrieval`) -> pool lebih besar -> rerank
  cross-encoder memakai query ASLI.
- **AWE**: `_DISPATCH["awe"]` baru — bot-filter dari `awe_botfilter_patch`
  DIPERTAHANKAN (helper-nya dipakai ulang), prefilter LIKE memakai token asli
  + perluasan kamus (bentuk mentah cocok untuk LIKE), skor memakai token
  TERNORMALISASI, top-pool di-rerank cross-encoder.
- **Sosmed**: pola yang sama pada FAQ pairs.
- Env: `RAG_SOURCES_PATCH=0` mematikan seluruh patch ini.

### 5. `web_app.py`
- Impor `rag_sources_patch` (setelah `handoff_routing_patch` agar membungkus
  versi terakhir tiap sumber).

### 6. `phase1_upgrade.py` (baru) — migrasi FTS v2 satu perintah
```
python phase1_upgrade.py            # migrasi indeks yang belum v2
python phase1_upgrade.py --force    # bangun ulang walau sudah v2
```
Tanpa GPU/model/internet. Untuk hasil terbaik: `pip install Sastrawi` dulu.

### 7. `requirements.txt`
- Tambah `Sastrawi>=1.0.1` (opsional, fail-soft).

## Urutan penerapan (PENTING)

1. **TUNGGU reindex Fase 0 (bge-m3) selesai** — rebuild FTS menulis ke
   `peraturan.db` / `sop.db` yang sama; menjalankannya bersamaan dengan reindex
   berisiko `database is locked`.
2. `git pull origin main` (setelah PR ini di-merge)
3. `pip install Sastrawi`
4. `python phase1_upgrade.py`
5. Restart aplikasi (`web_app.py`)
6. Uji `/rag-lab`:
   - "ketentuan penyerahan BKP dari luar daerah pabean ke kawasan berikat"
   - "peraturan yang mengatur SPLN"
   - bentuk berimbuhan: "menyerahkan", "diserahkan", "penyerahan"

## Rollback
- Set `RAG_SOURCES_PATCH=0` untuk mematikan patch sumber.
- `python phase1_upgrade.py --force` membangun ulang indeks; untuk kembali ke
  format lama cukup hapus penanda versi: `DELETE FROM peraturan_meta WHERE
  key='fts_version'` (dan `sop_meta`), lalu impor/rebuild ulang dari kode lama.
