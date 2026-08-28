# -*- coding: utf-8 -*-
"""rag_calibration.py — State & helper kalibrasi ambang cosine RAG (Point 3).

Menyimpan \"ambang cosine aktif\" (min_cos) PER-THREAD, sehingga proses evaluasi
(eval_harness / eval_sweep) bisa menyapu beberapa nilai ambang tanpa mengubah
konfigurasi global, sementara produksi memakai default dari env RAG_MIN_COS.

Tahap 4 #1: selain override per-thread (sweep) dan env, get_min_cos() kini juga
membaca knob PER-PROFIL dari rag.knob_store (precedence: override sweep >
store-profil > env > default). Profil aktif diset handler request via
set_profile(\"agent\"/\"chatbot\"). Bila profil tak diset atau knob_store tak
tersedia, perilaku kembali PERSIS ke env>default (fail-open).

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


# ---- Profil aktif per-thread (Tahap 4 #1) -----------------------------------
# Handler request (agent/chatbot) boleh menandai profil yang sedang dilayani
# via set_profile(); get_min_cos() lalu membaca knob per-profil dari
# rag.knob_store (precedence store>env>default). Bila profil TIDAK diset atau
# knob_store tak tersedia, perilaku kembali PERSIS ke env>default (fail-open).
def set_profile(p):
    _LOCAL.profile = p


def get_profile():
    return getattr(_LOCAL, "profile", None)


def reset_profile():
    _LOCAL.profile = None


def _store_min_cos():
    """Ambang efektif dari knob_store utk profil aktif; None bila tak bisa.

    Bila profil aktif None, knob_store.resolve() melewati lapisan store dan
    hanya membaca env>default — setara default_min_cos() (tanpa membuka DB)."""
    try:
        import rag.knob_store as _ks
    except Exception:
        return None
    try:
        v = _ks.resolve(get_profile(), "RAG_MIN_COS")
        return float(v) if v is not None else None
    except Exception:
        return None


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
    # 1) Override eksplisit per-thread (sweep /rag-eval) tetap TERTINGGI.
    v = getattr(_LOCAL, "min_cos", None)
    if v is not None:
        return v
    # 2) Knob per-profil (store>env>default) bila knob_store tersedia.
    sv = _store_min_cos()
    if sv is not None:
        return sv
    # 3) Fallback lama: env>default.
    return default_min_cos()


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
