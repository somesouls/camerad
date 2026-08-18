# CHANGES v14 — Fase 0 Upgrade RAG (model & kamus)

Ringkasan: menaikkan kelas model retrieval + memperkaya kamus istilah pajak,
**tanpa mengubah arsitektur** (hybrid FTS5+vektor, fusi RRF, reranker
 cross-encoder, kamus, AI rewrite, guardrail — semuanya tetap sama).

Latar: audit RAG Profil Agent terhadap rekomendasi arsitektur search untuk
peraturan pajak menemukan tiga gap P0 — model embedding & reranker satu-dua
kelas di bawah rekomendasi, dan kamus sinonim yang terlalu kecil (12 entri).

---

## Perubahan

### 1. `peraturan_semantic.py` — embedding default -> `BAAI/bge-m3`
- `model_id()` default: `intfloat/multilingual-e5-base` -> `BAAI/bge-m3`
  (1024-d, multilingual, lebih kuat untuk Bahasa Indonesia; env
  `PERATURAN_EMBED_MODEL` tetap bisa override).
- **Prefix teks otomatis per-model**: keluarga e5 -> `query: `/`passage: `;
  bge-m3 -> tanpa prefix (prefix ala e5 pada bge-m3 justru menurunkan kualitas).
  Override manual: `PERATURAN_EMBED_QUERY_PREFIX` / `PERATURAN_EMBED_PASSAGE_PREFIX`.
- Baru: `embed_dim()` — deteksi dimensi model aktif (baca dari model yang
  termuat; fallback peta nama model). Dipakai skrip `phase0_upgrade.py`.
- Konstanta `EMBED_DIM` dipertahankan sebagai fallback (1024 untuk bge-m3).
- Log startup kini mencetak model + device + dim.

### 2. `rag_reranker.py` — reranker default -> `BAAI/bge-reranker-v2-m3`
- `model_id()` default: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
  -> `BAAI/bge-reranker-v2-m3` (multilingual, jauh lebih akurat untuk pasangan
  pertanyaan–pasal berbahasa Indonesia). Env `RAG_RERANK_MODEL` tetap override.

### 3. `rag_kamus_db.py` — seed kamus diperkaya + merge idempoten
- `_DEFAULT_SEED`: 12 -> **115 entri** istilah perpajakan: SPLN, SPDN, BKP, JKP,
  DPP, kawasan berikat, luar daerah pabean, tempat penimbunan berikat, PPh
  21/22/23/25/26/29/4(2)/15, PPh final UMKM, Coretax, DJP Online, EFIN,
  e-Bupot, e-Faktur, e-Billing, restitusi, kompensasi, pemadanan NIK, PPS,
  transfer pricing, PMSE, sanksi, dst.
- `seed_default()` kini **merge idempoten**: hanya menambah entri yang belum
  ada (case-insensitive), TIDAK menimpa suntingan admin.
  Catatan: entri bawaan yang dihapus akan ditambahkan ulang — nonaktifkan
  (`aktif=0`) alih-alih menghapus bila ingin mematikan entri bawaan.

### 4. `phase0_upgrade.py` (baru) — eksekutor Fase 0 satu perintah
- Cek model & perangkat (cuda/cpu) untuk embedding + reranker.
- Deteksi mismatch dimensi vektor tersimpan vs model aktif.
- Reindex `peraturan_vec` & `sop_vec` bila perlu (`--reindex-all` / `--force`;
  bisa `--peraturan-only` / `--sop-only`).
- Cetak panduan langkah berikutnya (kalibrasi ambang via /rag-eval + uji query).

### 5. `.env.example` — variabel baru
- `PERATURAN_EMBED_MODEL`, `PERATURAN_EMBED_QUERY_PREFIX`,
  `PERATURAN_EMBED_PASSAGE_PREFIX`, `PERATURAN_EMBED_DEVICE`,
  `RAG_RERANK_MODEL`, `RAG_RERANK`, `RAG_RERANK_POOL`, `RAG_RERANK_DEVICE`,
  `RAG_MIN_COS` + catatan kewajiban reindex setelah ganti model.

---

## Dampak / breaking change
- **WAJIB reindex** setelah upgrade: vektor e5-base (768-d) tidak kompatibel
  dengan bge-m3 (1024-d). Tanpa reindex, jalur vektor akan gagal-hening
  (jatuh ke FTS saja). Jalankan: `python phase0_upgrade.py --reindex-all`.
- Unduhan model pertama kali: bge-m3 ~2,2 GB; bge-reranker-v2-m3 ~1,1 GB
  (sekali saja, butuh internet).
- VRAM terbatas? Set `PERATURAN_EMBED_DEVICE=cpu` dan/atau
  `RAG_RERANK_DEVICE=cpu` — latensi naik tapi tetap jalan.

## Rollback
Set env ke model lama lalu reindex ulang:
```
PERATURAN_EMBED_MODEL=intfloat/multilingual-e5-base
RAG_RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
python phase0_upgrade.py --force
```
