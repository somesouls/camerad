# -*- coding: utf-8 -*-
"""
peraturan_semantic.py
---------------------
Embedder semantik untuk korpus PERATURAN (menu Peraturan / sumber #5 RAG).

Bahasa peraturan bersifat formal/kaku sementara pertanyaan pengguna memakai
bahasa sehari-hari. Untuk pencocokan "pertanyaan pendek -> pasal panjang" kita
memakai model retrieval asimetris.

Model default (Fase 0): **BAAI/bge-m3** (1024-d, multilingual, kuat untuk
Bahasa Indonesia; mengungguli multilingual-e5-base pada benchmark retrieval
multilingual). Model lama `intfloat/multilingual-e5-base` tetap didukung —
set env PERATURAN_EMBED_MODEL untuk mengganti.

Prefix teks (PENTING, beda antar-model):
  * Keluarga e5 : WAJIB prefix 'query: ' (pertanyaan) & 'passage: ' (dokumen).
  * bge-m3      : TANPA prefix (dilatih tanpa prefix ala e5; menambahkannya
                  justru menurunkan kualitas).
Prefix dipilih otomatis dari nama model; bisa dioverride manual lewat env
PERATURAN_EMBED_QUERY_PREFIX / PERATURAN_EMBED_PASSAGE_PREFIX.

Berbeda dari jakai (yang menyimpan vektor di sqlite-vec/vec0), modul ini
mengembalikan vektor sebagai numpy float32 biasa; peraturan_db.py menyimpannya
sebagai BLOB dan menghitung cosine di Python. Dengan begitu tidak perlu binary
extension sqlite-vec di lingkungan camerad.

Gagal-anggun: bila numpy / sentence-transformers / model tak tersedia,
is_available()=False dan embed_*()=None sehingga retrieval jatuh ke FTS5/LIKE.

PENTING setelah ganti model: vektor lama TIDAK kompatibel. Jalankan reindex:
    python phase0_upgrade.py --reindex-all

Konfigurasi (env):
  PERATURAN_EMBED                 '1' (default) aktif; '0' matikan.
  PERATURAN_EMBED_MODEL           default 'BAAI/bge-m3'
  PERATURAN_EMBED_DEVICE          '' auto (cuda bila ada) / 'cuda' / 'cpu'
  PERATURAN_EMBED_BATCH           batch size encode saat reindex (default 32;
                                  naikkan ke 64-128 di GPU untuk mempercepat)
  PERATURAN_EMBED_QUERY_PREFIX    override prefix query ('' = tanpa prefix)
  PERATURAN_EMBED_PASSAGE_PREFIX  override prefix dokumen ('' = tanpa prefix)
"""
import os

try:
    import numpy as np
except Exception:
    np = None

# Konstanta fallback (model default v14). Untuk deteksi dinamis per-model,
# gunakan embed_dim().
EMBED_DIM = 1024  # BAAI/bge-m3

_MODEL = None
_MODEL_TRIED = False

# Peta dimensi fallback berdasar nama model (dipakai bila model belum termuat).
_DIM_BY_NAME = {
    "bge-m3": 1024,
    "multilingual-e5-large": 1024,
    "multilingual-e5-base": 768,
    "multilingual-e5-small": 384,
    "paraphrase-multilingual-mpnet-base-v2": 768,
}


def _enabled():
    return str(os.environ.get("PERATURAN_EMBED", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def model_id():
    return os.environ.get("PERATURAN_EMBED_MODEL", "BAAI/bge-m3")


def _prefix_env(name):
    """Kembalikan None bila env tak diset; '' berarti eksplisit tanpa prefix."""
    v = os.environ.get(name)
    return v if v is not None else None


def query_prefix():
    """Prefix untuk pertanyaan. e5 -> 'query: '; bge-m3 -> '' (tanpa prefix)."""
    v = _prefix_env("PERATURAN_EMBED_QUERY_PREFIX")
    if v is not None:
        return v
    return "query: " if "e5" in model_id().lower() else ""


def passage_prefix():
    """Prefix untuk dokumen. e5 -> 'passage: '; bge-m3 -> '' (tanpa prefix)."""
    v = _prefix_env("PERATURAN_EMBED_PASSAGE_PREFIX")
    if v is not None:
        return v
    return "passage: " if "e5" in model_id().lower() else ""


def embed_dim():
    """Dimensi embedding model aktif. Bila model sudah termuat, baca langsung
    dari model; kalau belum, tebak dari nama model (fallback). 0 = tak tahu."""
    if _MODEL is not None:
        try:
            d = _MODEL.get_sentence_embedding_dimension()
            if d:
                return int(d)
        except Exception:
            try:
                d = _MODEL.get_embedding_dimension()
                if d:
                    return int(d)
            except Exception:
                pass
    mid = model_id().lower()
    for key, d in _DIM_BY_NAME.items():
        if key in mid:
            return d
    return 0


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
        print("[peraturan_semantic] model=%s device=%s dim=%s"
              % (model_id(), dev, embed_dim()), flush=True)
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
    """Vektor pertanyaan (prefix otomatis sesuai model)."""
    t = (text or "").strip()
    if not t:
        return None
    return _encode(query_prefix() + t)


def embed_passage(text):
    """Vektor teks peraturan (prefix otomatis sesuai model)."""
    t = (text or "").strip()
    if not t:
        return None
    return _encode(passage_prefix() + t)


def embed_passages(texts):
    """Batch encode untuk reindex. Kembalikan np.ndarray[N, dim] atau None.

    Batch size via env PERATURAN_EMBED_BATCH (default 32). Di GPU, batch lebih
    besar (64-128) umumnya memangkas waktu reindex 2-4x."""
    m = _load_model()
    if m is None or np is None:
        return None
    pp = passage_prefix()
    try:
        bs = int(os.environ.get("PERATURAN_EMBED_BATCH", "32") or "32")
    except Exception:
        bs = 32
    try:
        arr = m.encode([pp + (t or "") for t in texts],
                       normalize_embeddings=True, convert_to_numpy=True,
                       batch_size=max(1, bs), show_progress_bar=False)
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
