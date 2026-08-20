# -*- coding: utf-8 -*-
"""
knowledge_semantic.py
---------------------
Retrieval SEMANTIK (SBERT) untuk pustaka pengetahuan analis: Glosarium,
Disambiguasi, Peta Intent & Maksud Analis, dan Katalog Intent.

Berbeda dgn match() berbasis keyword di tiap *_db.py, modul ini meng-embed
entri pustaka dengan SentenceTransformer (model sama seperti pipeline:
paraphrase-multilingual-mpnet-base-v2) lalu memeringkat berdasarkan cosine
similarity, sehingga tahan terhadap parafrase / beda kata / salah ketik.

Desain:
  - Model dimuat sekali (lazy) & disimpan (singleton). Embedding korpus di-cache
    per pustaka; otomatis dibangun ulang bila jumlah/updated_at baris berubah.
  - Per entri: tiap "key" (istilah/alias/contoh/cakupan/deskripsi) di-embed,
    skor entri = MAX cosine antar key-nya (recall lebih baik utk kalimat pendek).
  - Gagal-anggun: bila torch / sentence-transformers / model tak tersedia,
    is_available()=False dan semantic_match() mengembalikan {} (pemanggil
    otomatis jatuh ke keyword).

Konfigurasi (env):
  KNOWLEDGE_SEMANTIC           '1' (default) aktif; '0' matikan.
  KNOWLEDGE_SEMANTIC_MODEL     default 'paraphrase-multilingual-mpnet-base-v2'
  KNOWLEDGE_SEMANTIC_MIN_SCORE default '0.45' (ambang cosine)
  KNOWLEDGE_SEMANTIC_DEVICE    '' auto (cuda bila ada) / 'cuda' / 'cpu'
"""
import os

try:
    import numpy as np
except Exception:
    np = None

from knowledge import glossary_db as gdb
from knowledge import disambig_db as ddb
from knowledge import intentmap_db as imdb

_MODEL = None
_MODEL_TRIED = False
_INDEX = {}                # lib -> {sig, entries, emb, owner}
_ENCODER_OVERRIDE = None   # hook pengujian: fn(list[str]) -> np.ndarray[N,d] (ter-normalisasi)
_CORPUS_OVERRIDE = None     # hook pengujian: {lib: [entry_dict,...]}
_MAX_KEYS = 16
_LIBS = ("glosarium", "disambiguasi", "intentmap", "katalog")


def _enabled():
    return str(os.environ.get("KNOWLEDGE_SEMANTIC", "1")).strip().lower() not in ("0", "false", "no", "off")


def _min_score():
    try:
        return float(os.environ.get("KNOWLEDGE_SEMANTIC_MIN_SCORE", "0.45"))
    except Exception:
        return 0.45


def _model_id():
    return os.environ.get("KNOWLEDGE_SEMANTIC_MODEL", "paraphrase-multilingual-mpnet-base-v2")


# ---- hook pengujian ----
def set_encoder(fn):
    global _ENCODER_OVERRIDE
    _ENCODER_OVERRIDE = fn
    _INDEX.clear()


def set_corpus(corpus):
    global _CORPUS_OVERRIDE
    _CORPUS_OVERRIDE = corpus
    _INDEX.clear()


def _load_model():
    global _MODEL, _MODEL_TRIED
    if _MODEL is not None:
        return _MODEL
    if _MODEL_TRIED:
        return None
    _MODEL_TRIED = True
    try:
        import torch
        from sentence_transformers import SentenceTransformer
        dev = os.environ.get("KNOWLEDGE_SEMANTIC_DEVICE", "").strip()
        if not dev:
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        _MODEL = SentenceTransformer(_model_id(), device=dev)
    except Exception:
        _MODEL = None
    return _MODEL


def _encode(texts):
    texts = list(texts)
    if _ENCODER_OVERRIDE is not None:
        return _ENCODER_OVERRIDE(texts)
    m = _load_model()
    if m is None or np is None:
        return None
    emb = m.encode(texts, convert_to_numpy=True, normalize_embeddings=True,
                   batch_size=32, show_progress_bar=False)
    return np.asarray(emb, dtype="float32")


def is_available():
    if not _enabled():
        return False
    if _ENCODER_OVERRIDE is not None:
        return True
    if np is None:
        return False
    return _load_model() is not None


def _clip(s):
    return str(s or "").strip()[:200]


def _keys_glossary(d):
    ks = [d.get("term", ""), d.get("nama_panjang", "")]
    ks += list(d.get("aliases") or [])
    ks += list(d.get("contoh_pertanyaan") or [])
    ks += list(d.get("masalah_umum") or [])
    if d.get("definisi"):
        ks.append(d["definisi"])
    return [k for k in (_clip(x) for x in ks) if k]


def _keys_disambig(d):
    ks = [d.get("pemicu", "")]
    ks += list(d.get("pola") or [])
    for k in (d.get("kandidat") or []):
        if isinstance(k, dict) and k.get("label"):
            ks.append(k["label"])
    if d.get("catatan"):
        ks.append(d["catatan"])
    return [k for k in (_clip(x) for x in ks) if k]


def _keys_intentmap(d):
    ks = [d.get("intent", "")]
    ks += list(d.get("cakupan") or [])
    ks += list(d.get("contoh_utterance") or [])
    if d.get("alasan"):
        ks.append(d["alasan"])
    return [k for k in (_clip(x) for x in ks) if k]


def _keys_catalog(d):
    ks = [d.get("intent", "")]
    if d.get("deskripsi_maksud"):
        ks.append(d["deskripsi_maksud"])
    if d.get("deskripsi_cakupan"):
        ks.append(d["deskripsi_cakupan"])
    ks += list(d.get("training_phrase_contoh") or [])
    return [k for k in (_clip(x) for x in ks) if k]


_KEYFN = {
    "glosarium": _keys_glossary,
    "disambiguasi": _keys_disambig,
    "intentmap": _keys_intentmap,
    "katalog": _keys_catalog,
}


def _sig(c, table):
    try:
        r = c.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM %s" % table).fetchone()
        return (int(r[0]), str(r[1] or ""))
    except Exception:
        return (0, "")


def _fetch_entries(lib):
    """Kembalikan (rows, sig). Memakai override korpus bila di-set (pengujian)."""
    if _CORPUS_OVERRIDE is not None:
        rows = list(_CORPUS_OVERRIDE.get(lib, []))
        return rows, (len(rows), "override")
    if lib == "glosarium":
        c = gdb.init_db(gdb.connect())
        try:
            return gdb.list_terms(c, status="aktif", limit=5000), _sig(c, "glossary")
        finally:
            c.close()
    if lib == "disambiguasi":
        c = ddb.init_db(ddb.connect())
        try:
            return ddb.list_rules(c, status="aktif", limit=5000), _sig(c, "disambig")
        finally:
            c.close()
    if lib == "intentmap":
        c = imdb.init_db(imdb.connect())
        try:
            return imdb.list_intents(c, status="aktif", limit=5000), _sig(c, "intentmap")
        finally:
            c.close()
    if lib == "katalog":
        c = imdb.init_catalog(imdb.connect())
        try:
            rows = [d for d in imdb.catalog_list(c, limit=5000)
                    if (d.get("deskripsi_maksud") or d.get("deskripsi_cakupan"))]
            return rows, _sig(c, "intentmap_catalog")
        finally:
            c.close()
    return [], (0, "")


def _build_index(lib):
    rows, sig = _fetch_entries(lib)
    cached = _INDEX.get(lib)
    if cached and cached.get("sig") == sig:
        return cached
    keyfn = _KEYFN.get(lib, lambda d: [])
    entries, keys, owner = [], [], []
    for d in rows:
        eks = keyfn(d)[:_MAX_KEYS]
        if not eks:
            continue
        ei = len(entries)
        entries.append(d)
        for k in eks:
            keys.append(k)
            owner.append(ei)
    emb = _encode(keys) if keys else None
    idx = {
        "sig": sig,
        "entries": entries,
        "emb": emb,
        "owner": (np.asarray(owner) if (np is not None and owner) else owner),
    }
    _INDEX[lib] = idx
    return idx


def semantic_match(query, per_lib_limit=4, min_score=None):
    """Kembalikan {lib: [entry_dict,...]} berperingkat cosine >= ambang.
    Bentuk entry sama seperti row_to_dict/_cat_row masing-masing pustaka,
    sehingga bisa langsung dirender oleh build_context_text terkait."""
    out = {}
    q = (query or "").strip()
    if not q or not is_available():
        return out
    ms = _min_score() if min_score is None else float(min_score)
    qv = _encode([q])
    if qv is None or len(qv) == 0:
        return out
    qv = qv[0]
    for lib in _LIBS:
        try:
            idx = _build_index(lib)
        except Exception:
            continue
        emb = idx.get("emb")
        entries = idx.get("entries")
        owner = idx.get("owner")
        if emb is None or not entries:
            continue
        sims = emb @ qv  # emb ter-normalisasi & qv ter-normalisasi -> cosine
        best = {}
        for j in range(len(sims)):
            ei = int(owner[j])
            s = float(sims[j])
            if s > best.get(ei, -1.0):
                best[ei] = s
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        picks = [entries[ei] for ei, s in ranked if s >= ms][:per_lib_limit]
        if picks:
            out[lib] = picks
    return out
