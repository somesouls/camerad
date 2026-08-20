# -*- coding: utf-8 -*-
"""rag_rerank_patch.py — Tahap 5: reranker + query rewriting untuk retrieval.

Membungkus peraturan_db.search agar:
  1. Query DIPERLUAS lebih dulu (rag_rewrite.untuk_retrieval): kamus sinonim +
     rewriting AI istilah/pasal -> menutup jurang bahasa awam vs bahasa hukum.
  2. Kandidat DINILAI ULANG (rag_reranker.rerank) dengan cross-encoder memakai
     query ASLI -> urutan relevansi jauh lebih akurat.

Dipasang lewat web_app.py (import) SETELAH rag_successor_patch dan SEBELUM
rag_calibration_patch, agar gerbang cosine (rag_calibration_patch) tetap menilai
kemiripan terhadap query ASLI: gate memanggil wrapper ini dengan query asli,
wrapper memperluas query HANYA untuk mengambil kandidat, lalu rerank memakai
query asli; gate menilai cosine query asli atas hasil.

Gagal-anggun: bila modul rewrite/reranker atau modelnya tak tersedia, perilaku
kembali seperti semula (hybrid FTS5 + e5, dipotong k).
"""
import os

import peraturan.db as _pdb

try:
    import rag.rewrite as _rw
except Exception:            # pragma: no cover
    _rw = None
try:
    import rag.reranker as _rr
except Exception:            # pragma: no cover
    _rr = None

_orig_search = _pdb.search


def _pool_size(k):
    try:
        base = int(os.environ.get("RAG_RERANK_POOL", "30"))
    except Exception:
        base = 30
    return max(int(k or 10), base)


def _search_rerank(query, k=10, status_list=("berlaku",), conn=None):
    q = (query or "").strip()
    # (1) perluas query utk retrieval (kamus + AI). Query ASLI tetap dipakai rerank.
    q_eff = q
    if _rw is not None and q:
        try:
            q_eff = _rw.untuk_retrieval(q) or q
        except Exception:
            q_eff = q
    # (2) ambil pool lebih besar bila reranker aktif, agar ada ruang urut ulang.
    use_rr = False
    try:
        use_rr = _rr is not None and _rr.is_available()
    except Exception:
        use_rr = False
    ambil = _pool_size(k) if use_rr else k
    rows = _orig_search(q_eff, k=ambil, status_list=status_list, conn=conn)
    if use_rr and rows:
        try:
            rows = _rr.rerank(q, rows, top_k=k)
        except Exception:
            rows = rows[:k]
    else:
        rows = rows[:k]
    return rows


_pdb.search = _search_rerank

try:
    print("[rag_rerank_patch] aktif (rerank=%s, rewrite=%s)" % (_rr is not None, _rw is not None))
except Exception:
    pass
