# -*- coding: utf-8 -*-
"""rag_calibration.py — State & helper kalibrasi ambang cosine RAG (Point 3).

Menyimpan "ambang cosine aktif" (min_cos) PER-THREAD, sehingga proses evaluasi
(eval_harness / eval_sweep) bisa menyapu beberapa nilai ambang tanpa mengubah
konfigurasi global, sementara produksi memakai default dari env RAG_MIN_COS.

Juga menyediakan penilai cosine yang dipakai rag_calibration_patch untuk
menggerbang hasil retrieval:
  - skor_peraturan(query, ids) -> {id: cosine} memakai vektor e5 tersimpan.
  - skor_intent_terbaik(query) -> cosine tertinggi katalog/intentmap (SBERT).

Semua GAGAL-ANGGUN: bila numpy / model / vektor tak tersedia, penilai
mengembalikan kosong/None sehingga pemanggil TIDAK menyaring (fail-open).
"""
import os
import threading

try:
    import numpy as np
except Exception:            # pragma: no cover
    np = None

_LOCAL = threading.local()


def default_min_cos():
    """Ambang bawaan produksi dari env RAG_MIN_COS (0 = gerbang mati)."""
    try:
        return float(os.environ.get("RAG_MIN_COS", "0") or 0)
    except Exception:
        return 0.0


def set_min_cos(v):
    """Set ambang aktif untuk thread ini (None = pakai default env)."""
    if v is None:
        _LOCAL.min_cos = None
        return
    try:
        _LOCAL.min_cos = float(v)
    except Exception:
        _LOCAL.min_cos = None


def reset_min_cos():
    _LOCAL.min_cos = None


def get_min_cos():
    v = getattr(_LOCAL, "min_cos", None)
    if v is None:
        return default_min_cos()
    return v


def aktif():
    """True bila ambang > 0 (gerbang menyala)."""
    try:
        return get_min_cos() > 0.0
    except Exception:
        return False


# ---- penilai cosine PERATURAN (e5; vektor tersimpan di tabel peraturan_vec) --
def skor_peraturan(query, ids):
    """Kembalikan {id: cosine} untuk daftar id peraturan terhadap query.
    Kosong bila embedding/numpy/vektor tak tersedia (pemanggil fail-open)."""
    out = {}
    if np is None or not ids:
        return out
    try:
        import peraturan.semantic as psem
        import peraturan.db as pdb
    except Exception:
        return out
    try:
        qv = psem.embed_query(query)
    except Exception:
        qv = None
    if qv is None:
        return out
    try:
        qv = np.asarray(qv, dtype="float32")
    except Exception:
        return out
    conn = None
    try:
        conn = pdb.init_db(pdb.connect())
        want = [i for i in ids if i]
        if not want:
            return out
        ph = ",".join("?" for _ in want)
        rows = conn.execute(
            "SELECT id, emb FROM peraturan_vec WHERE id IN (%s)" % ph,
            tuple(want),
        ).fetchall()
        for r in rows:
            v = psem.from_blob(r["emb"])
            if v is None:
                continue
            try:
                out[r["id"]] = float(np.dot(np.asarray(v, dtype="float32"), qv))
            except Exception:
                continue
    except Exception:
        return out
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return out


# ---- penilai cosine INTENT (SBERT; index milik knowledge_semantic) -----------
def skor_intent_terbaik(query):
    """Kembalikan cosine TERTINGGI query terhadap pustaka intent (katalog +
    intentmap). None bila model semantik tak tersedia (pemanggil fail-open)."""
    if np is None:
        return None
    try:
        import knowledge.semantic as ksem
    except Exception:
        return None
    try:
        if not ksem.is_available():
            return None
    except Exception:
        return None
    try:
        qv = ksem._encode([query])
    except Exception:
        qv = None
    if qv is None or len(qv) == 0:
        return None
    qv = qv[0]
    best = None
    for lib in ("katalog", "intentmap"):
        try:
            idx = ksem._build_index(lib)
        except Exception:
            continue
        emb = idx.get("emb")
        entries = idx.get("entries")
        if emb is None or not entries:
            continue
        try:
            sims = emb @ qv
            m = float(np.max(sims))
        except Exception:
            continue
        if best is None or m > best:
            best = m
    return best
