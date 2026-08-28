# -*- coding: utf-8 -*-
"""Paket cluster peraturan (PR-4 reorg). Impor lama (import peraturan_<mod>) didukung sementara lewat shim di root."""

# --- Tahap 2: guard dedup ingest (env-gated, default off) ---
# Muat db dulu (agar peraturan.db lengkap), lalu pasang guard yang membungkus
# upsert_peraturan supaya salinan scraping ganda (nomor+tahun+isi identik,
# source_id beda) tak menambah baris baru. Default OFF (RAG_INGEST_DEDUP=1);
# nol dampak ke retrieval/eval (upsert_peraturan hanya dipakai saat ingest).
try:
    from . import db as _db  # noqa: F401  (pastikan peraturan.db termuat penuh dulu)
    import rag.ingest_dedup_patch  # noqa: F401
except Exception:
    pass
