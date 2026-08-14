# -*- coding: utf-8 -*-
"""rag_reranker.py — Cross-encoder reranker untuk retrieval RAG (Tahap 5).

Retrieval hybrid (FTS5 + e5 bi-encoder) menghasilkan kandidat secara cepat,
tetapi urutannya belum tentu paling relevan. Reranker cross-encoder membaca
pasangan (pertanyaan, kandidat) sekaligus lalu memberi skor relevansi yang jauh
lebih akurat — inilah tahap yang meniru cara mesin pencari modern mengurutkan
"paling relevan -> kurang relevan".

Model default multilingual & ringan (mmarco-mMiniLMv2). Memakai
sentence-transformers.CrossEncoder yang SUDAH menjadi dependency (dipakai e5),
jadi tidak ada paket baru.

Konfigurasi (env):
  RAG_RERANK          '1' (default) aktif; '0' matikan.
  RAG_RERANK_MODEL    default 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
  RAG_RERANK_POOL     jumlah kandidat maksimum yang dinilai ulang (default 30)
  RAG_RERANK_DEVICE   '' auto (cuda bila ada) / 'cuda' / 'cpu'

Gagal-anggun: bila model/paket tak tersedia atau error, rerank() mengembalikan
urutan asli (dipotong top_k) sehingga retrieval tetap jalan.
"""
import os

_MODEL = None
_MODEL_TRIED = False


def _enabled():
    return str(os.environ.get("RAG_RERANK", "1")).strip().lower() not in ("0", "false", "no", "off")


def model_id():
    return os.environ.get("RAG_RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")


def _pool():
    try:
        return max(1, int(os.environ.get("RAG_RERANK_POOL", "30")))
    except Exception:
        return 30


def _load_model():
    global _MODEL, _MODEL_TRIED
    if _MODEL is not None:
        return _MODEL
    if _MODEL_TRIED:
        return None
    _MODEL_TRIED = True
    if not _enabled():
        return None
    try:
        from sentence_transformers import CrossEncoder
        dev = os.environ.get("RAG_RERANK_DEVICE", "").strip()
        if not dev:
            try:
                import torch
                dev = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                dev = "cpu"
        _MODEL = CrossEncoder(model_id(), device=dev, max_length=512)
    except Exception:
        _MODEL = None
    return _MODEL


def is_available():
    if not _enabled():
        return False
    return _load_model() is not None


def _cand_text(row):
    try:
        d = row if isinstance(row, dict) else dict(row)
    except Exception:
        return ""
    judul = str(d.get("judul") or "").strip()
    isi = str(d.get("isi") or "").strip()
    txt = (judul + ". " + isi).strip(". ").strip()
    return txt[:1800]


def rerank(query, rows, top_k=None):
    """Urutkan ulang `rows` (list dict) berdasar relevansi ke `query`.

    Menambahkan kunci 'rerank_skor' pada tiap baris yang dinilai. Bila model tak
    tersedia, kembalikan rows[:top_k] tanpa perubahan urutan.
    """
    rows = list(rows or [])
    q = (query or "").strip()
    if not rows or not q:
        return rows[:top_k] if top_k else rows
    m = _load_model()
    if m is None:
        return rows[:top_k] if top_k else rows
    pool = rows[:_pool()]
    rest = rows[_pool():]
    try:
        pairs = [(q, _cand_text(r)) for r in pool]
        scores = m.predict(pairs, show_progress_bar=False)
        scored = []
        for r, s in zip(pool, scores):
            try:
                if isinstance(r, dict):
                    r["rerank_skor"] = round(float(s), 4)
            except Exception:
                pass
            scored.append((float(s), r))
        scored.sort(key=lambda x: -x[0])
        ordered = [r for _, r in scored] + rest
    except Exception:
        ordered = rows
    return ordered[:top_k] if top_k else ordered
