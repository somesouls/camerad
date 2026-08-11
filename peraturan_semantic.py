# -*- coding: utf-8 -*-
"""
peraturan_semantic.py
---------------------
Embedder semantik untuk korpus PERATURAN (menu Peraturan / sumber #5 RAG).

Bahasa peraturan bersifat formal/kaku sementara pertanyaan pengguna memakai
bahasa sehari-hari. Untuk pencocokan "pertanyaan pendek -> pasal panjang" kita
memakai model e5 (intfloat/multilingual-e5-base) yang dirancang untuk retrieval
asimetris dengan prefix 'query:' / 'passage:'.

Berbeda dari jakai (yang menyimpan vektor di sqlite-vec/vec0), modul ini
mengembalikan vektor sebagai numpy float32 biasa; peraturan_db.py menyimpannya
sebagai BLOB dan menghitung cosine di Python. Dengan begitu tidak perlu binary
extension sqlite-vec di lingkungan camerad.

Gagal-anggun: bila numpy / sentence-transformers / model tak tersedia,
is_available()=False dan embed_*()=None sehingga retrieval jatuh ke FTS5/LIKE.

Konfigurasi (env):
  PERATURAN_EMBED           '1' (default) aktif; '0' matikan.
  PERATURAN_EMBED_MODEL     default 'intfloat/multilingual-e5-base'
  PERATURAN_EMBED_DEVICE    '' auto (cuda bila ada) / 'cuda' / 'cpu'
"""
import os

try:
    import numpy as np
except Exception:
    np = None

EMBED_DIM = 768  # multilingual-e5-base

_MODEL = None
_MODEL_TRIED = False


def _enabled():
    return str(os.environ.get("PERATURAN_EMBED", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def model_id():
    return os.environ.get("PERATURAN_EMBED_MODEL", "intfloat/multilingual-e5-base")


def _load_model():
    global _MODEL, _MODEL_TRIED
    if _MODEL is not None:
        return _MODEL
    if _MODEL_TRIED:
        return None
    _MODEL_TRIED = True
    if not _enabled() or np is None:
        return None
    try:
        import torch
        from sentence_transformers import SentenceTransformer
        dev = os.environ.get("PERATURAN_EMBED_DEVICE", "").strip()
        if not dev:
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        _MODEL = SentenceTransformer(model_id(), device=dev)
    except Exception:
        _MODEL = None
    return _MODEL


def is_available():
    if not _enabled() or np is None:
        return False
    return _load_model() is not None


def _encode(text):
    m = _load_model()
    if m is None or np is None:
        return None
    try:
        v = m.encode(text, normalize_embeddings=True, convert_to_numpy=True,
                     show_progress_bar=False)
        return np.asarray(v, dtype="float32")
    except Exception:
        return None


def embed_query(text):
    """Vektor pertanyaan (prefix e5 'query: ')."""
    t = (text or "").strip()
    if not t:
        return None
    return _encode("query: " + t)


def embed_passage(text):
    """Vektor teks peraturan (prefix e5 'passage: ')."""
    t = (text or "").strip()
    if not t:
        return None
    return _encode("passage: " + t)


def embed_passages(texts):
    """Batch encode untuk reindex. Kembalikan np.ndarray[N, dim] atau None."""
    m = _load_model()
    if m is None or np is None:
        return None
    try:
        arr = m.encode(["passage: " + (t or "") for t in texts],
                       normalize_embeddings=True, convert_to_numpy=True,
                       batch_size=32, show_progress_bar=False)
        return np.asarray(arr, dtype="float32")
    except Exception:
        return None


def to_blob(vec):
    """numpy float32 -> bytes untuk disimpan di SQLite BLOB."""
    if np is None or vec is None:
        return None
    return np.asarray(vec, dtype="float32").tobytes()


def from_blob(blob):
    """bytes -> numpy float32 (ter-normalisasi saat disimpan)."""
    if np is None or not blob:
        return None
    try:
        return np.frombuffer(blob, dtype="float32")
    except Exception:
        return None
