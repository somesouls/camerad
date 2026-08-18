# CHANGES v15 — Fase 0.1: reindex resume + batch embedding via env

Hotfix kecil di atas v14 (Fase 0) agar reindex embedding lebih aman & lebih
cepat — terinspirasi dari run produksi pertama bge-m3 (33,9 ribu unit).

## Perubahan

### 1. `peraturan_db.py` & `sop_db.py` — `reindex(..., resume=True)`
- Default baru: reindex **melewati** baris yang sudah punya vektor berdimensi
  model aktif (dibaca dari `peraturan_vec.dim` / `sop_vec.dim`).
- Efek: Ctrl+C / interupsi / ganti batch size TIDAK kehilangan progres —
  jalankan ulang skrip dan proses melanjutkan sisa unit.
- `resume=False` mengembalikan perilaku lama (embed ulang semua); dipakai
  `phase0_upgrade.py --force`.
- Keluaran reindex kini menyertakan `skipped` & `total`.

### 2. `peraturan_semantic.py` — `PERATURAN_EMBED_BATCH`
- Batch size encode (sebelumnya tetap 32) kini bisa diset via env.
- Di GPU, batch 64–128 umumnya memangkas waktu reindex 2–4×.
  Contoh PowerShell: `$env:PERATURAN_EMBED_BATCH=96`
- `embed_dim()` lebih tahan-ubah: fallback ke `get_embedding_dimension()`
  (rename baru di sentence-transformers) bila method lama tak ada.

### 3. `phase0_upgrade.py`
- Mode default/`--reindex-all` kini memakai reindex RESUME; `--force` = penuh.
- Panduan akhir ditambah tips batch + keamanan interupsi.

### 4. `.env.example`
- Tambahan komentar `PERATURAN_EMBED_BATCH`.
