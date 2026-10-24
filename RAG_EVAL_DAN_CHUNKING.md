# Evaluasi Retrieval & Contextual Chunking (RAG Peraturan)

Dokumen ini menjelaskan dua alat baru yang bersifat **aditif dan aman**
(tidak mengubah perilaku engine): `eval_retrieval.py` dan `reindex_context.py`.

## 1. Kenapa ini penting

Harness lama (`/rag-eval`, `eval_harness.py`) menilai **jawaban akhir** lewat
LLM-judge. Itu tidak memberi tahu apakah kesalahan berasal dari RETRIEVAL
(pasal yang benar tak ketemu) atau dari LLM. `eval_retrieval.py` mengukur
langsung mutu retrieval — prediktor terkuat mutu RAG. Kalau pasal yang benar
tidak masuk top-k, LLM sebagus apa pun tak akan menjawab benar.

## 2. Membuat gold set

```bash
cp eval_retrieval_goldset.example.jsonl eval_retrieval_goldset.jsonl
```

Format tiap baris (JSONL):
```json
{"pertanyaan": "npwp ku ilang gmn ngurusnya", "gold": [{"jenis": "PER", "nomor": "PER-04/PJ/2020", "pasal": "10"}]}
```
- `pertanyaan` (wajib): tulis apa adanya (informal/slang/typo) seperti user asli.
- `gold`: daftar `{jenis, nomor, pasal?}`. `pasal` boleh dikosongkan untuk
  mencocokkan di level peraturan.
- `gold_ids`: alternatif, daftar `id` unit persis (kolom `peraturan_unit.id`).
- **Gunakan nilai `jenis`/`nomor`/`id` PERSIS seperti di DB** (cek menu admin
  peraturan atau `peraturan_db.peraturan_tersusun(nomor)`).

Sasaran awal realistis: 50–150 pertanyaan tersering dari livechat.

## 3. Menjalankan evaluasi

```bash
# Apakah query rewriting membantu? Bandingkan dua ini:
python eval_retrieval.py --query-mode raw
python eval_retrieval.py --query-mode rewrite

# Ukur dampak patch retrieval (dense/lexical split + xref):
python eval_retrieval.py --patches rag_rerank_patch --out laporan.json
```
Keluaran: `hit@k`, `recall@k`, `MRR`. Naiknya `recall@5`/`MRR` = retrieval membaik.

## 4. Contextual chunking (`reindex_context.py`)

Vektor e5 saat ini dibangun dari `judul + isi` saja; konteks struktural
(jenis, nomor, bab, pasal, hierarchy) hilang. `reindex_context.py` membangun
ulang vektor dari `judul + identitas + hierarki + isi`.

```bash
python reindex_context.py --dry-run     # intip teks kontekstual dulu
python reindex_context.py               # re-embed seluruh korpus
```
Reversibel (kembali ke perilaku lama):
```bash
python -c "import peraturan_db; print(peraturan_db.reindex())"
```

### Runbook pengukuran (disarankan)
1. `python eval_retrieval.py --out sebelum.json`  ← baseline
2. `python reindex_context.py`
3. `python eval_retrieval.py --out sesudah.json`
4. Bandingkan `recall@5` & `MRR`: `sebelum.json` vs `sesudah.json`.

Jika naik → pertimbangkan menjadikan permanen (patch pembangun teks embed di
`peraturan_db._sync_vec` + `reindex` agar ingesti baru ikut kontekstual —
lihat catatan PR). Jika turun → tinggal reindex balik.

> Catatan: `reindex_context.py` hanya menyentuh tabel `peraturan_vec`. Ingesti
> BARU (`upsert_peraturan`) masih memakai `judul + isi`; jalankan ulang script
> ini setelah ingesti besar, atau minta patch permanen.
