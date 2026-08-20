# -*- coding: utf-8 -*-
"""rag_intent_semantic.py — Retrieval SEMANTIK untuk Katalog Intent Dialogflow.

Melengkapi pencocokan LEKSIKAL (hitung-token) pada rag_engine._ctx_dialogflow
yang lemah terhadap parafrase/sinonim/salah ketik, mis.:
  - "Mengubah pekerjaan jadi pegawai negeri sipil" -> intent "...Perubahan Data"
  - "error PPN"                                    -> intent "Kode Error..."
  - "saya perlu efin untuk lapor spt"              -> intent "...Lupa EFIN"

Modul ini meng-embed tiap intent katalog (nama intent + contoh training phrase +
deskripsi + cuplikan jawaban) memakai SentenceTransformer lalu memeringkat
berdasarkan cosine similarity terhadap pertanyaan. Skor entri = MAX cosine antar
"key"-nya (recall lebih baik untuk kalimat pendek).

Berbeda dgn knowledge_semantic.py (yang HANYA mengindeks intent ber-deskripsi
analis), modul ini mengindeks SELURUH intent katalog aktif — termasuk yang belum
punya deskripsi — karena justru training phrase-lah sinyal terkuat untuk recall
intent Dialogflow (persis frasa yang diuji Metode 1).

Desain:
  - Model dimuat sekali (lazy, singleton). Embedding korpus di-cache; otomatis
    dibangun ulang bila (COUNT, MAX(updated_at)) katalog berubah.
  - Gagal-anggun: bila numpy / torch / sentence-transformers / model tak
    tersedia, is_available()=False dan rank() -> [] (pemanggil otomatis jatuh
    ke leksikal saja).

Konfigurasi (env):
  RAG_INTENT_SEMANTIC          '1' (default) aktif; '0' matikan.
  RAG_INTENT_SEMANTIC_MODEL    default = KNOWLEDGE_SEMANTIC_MODEL /
                               'paraphrase-multilingual-mpnet-base-v2'
  RAG_INTENT_SEMANTIC_MIN      ambang cosine (default '0.42')
  RAG_INTENT_SEMANTIC_DEVICE   '' auto (cuda bila ada) / 'cuda' / 'cpu'
  RAG_INTENT_SEMANTIC_MAXKEYS  jumlah key maksimum per intent (default 14)
"""
import os
import re
import json

try:
    import numpy as np
except Exception:            # pragma: no cover
    np = None

try:
    import knowledge.intentmap_db as imdb
except Exception:            # pragma: no cover
    imdb = None

_MODEL = None
_MODEL_TRIED = False
_INDEX = None                # {sig, entries, emb, owner}
_ENCODER_OVERRIDE = None      # hook pengujian: fn(list[str]) -> np.ndarray[N,d] ternormalisasi

_SQL_CAT = (
    "SELECT intent, deskripsi_maksud, deskripsi_cakupan, jawaban_cuplikan, "
    "training_phrase_contoh FROM intentmap_catalog "
    "WHERE COALESCE(sumber_status,'aktif')!='hilang' "
    "AND COALESCE(soft_deleted,0)=0"
)


def _enabled():
    return str(os.environ.get("RAG_INTENT_SEMANTIC", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _min_score():
    try:
        return float(os.environ.get("RAG_INTENT_SEMANTIC_MIN", "0.42"))
    except Exception:
        return 0.42


def _maxkeys():
    try:
        return max(2, int(os.environ.get("RAG_INTENT_SEMANTIC_MAXKEYS", "14")))
    except Exception:
        return 14


def _model_id():
    return (os.environ.get("RAG_INTENT_SEMANTIC_MODEL")
            or os.environ.get("KNOWLEDGE_SEMANTIC_MODEL")
            or "paraphrase-multilingual-mpnet-base-v2")


def device_info():
    """Diagnostik: perangkat & build torch yang sebenarnya dipakai retrieval
    semantik intent. Jika cuda_available=False padahal PC ber-GPU, biasanya
    torch yang terinstal adalah build CPU-only (lihat catatan requirements.txt)."""
    info = {"enabled": _enabled(), "model": _model_id(),
            "device_env": os.environ.get("RAG_INTENT_SEMANTIC_DEVICE", "").strip()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_build"] = getattr(torch.version, "cuda", None)
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["device"] = "cuda" if (not info["device_env"] and info["cuda_available"]) else (info["device_env"] or "cpu")
    except Exception as e:
        info["torch"] = None
        info["error"] = str(e)[:120]
    return info


# ---- hook pengujian ----
def set_encoder(fn):
    """Set encoder tiruan untuk pengujian: fn(list[str]) -> np.ndarray[N,d]
    (ter-normalisasi). Mengosongkan cache indeks."""
    global _ENCODER_OVERRIDE, _INDEX
    _ENCODER_OVERRIDE = fn
    _INDEX = None


def reset_cache():
    global _INDEX
    _INDEX = None


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
        dev = os.environ.get("RAG_INTENT_SEMANTIC_DEVICE", "").strip()
        cuda_ok = bool(torch.cuda.is_available())
        if not dev:
            dev = "cuda" if cuda_ok else "cpu"
        _MODEL = SentenceTransformer(_model_id(), device=dev)
        # Log diagnostik: ungkap perangkat NYATA. device=cpu padahal PC ber-GPU
        # => hampir pasti torch build CPU-only (cuda_build=None).
        print("[rag_intent_semantic] model=%s device=%s cuda_available=%s torch=%s cuda_build=%s"
              % (_model_id(), dev, cuda_ok, torch.__version__,
                 getattr(torch.version, "cuda", None)), flush=True)
    except Exception:
        _MODEL = None
    return _MODEL


def _encode(texts):
    texts = list(texts)
    if _ENCODER_OVERRIDE is not None:
        return _ENCODER_OVERRIDE(texts)
    if np is None:
        return None
    m = _load_model()
    if m is None:
        return None
    try:
        emb = m.encode(texts, convert_to_numpy=True, normalize_embeddings=True,
                       batch_size=32, show_progress_bar=False)
        return np.asarray(emb, dtype="float32")
    except Exception:
        return None


def is_available():
    if not _enabled():
        return False
    if _ENCODER_OVERRIDE is not None:
        return True
    if np is None or imdb is None:
        return False
    return _load_model() is not None


# ---- pembangunan indeks ----
def _humanize(name):
    s = str(name or "").replace("_", " ").replace("/", " ")
    return re.sub(r"\s+", " ", s).strip()


def _clip(s, n=240):
    return str(s or "").strip()[:n]


def _json_list(v):
    if isinstance(v, list):
        return v
    try:
        x = json.loads(v) if v else []
        return x if isinstance(x, list) else []
    except Exception:
        return []


def _keys(d):
    """Kumpulan teks yang di-embed per intent (nama + frasa + deskripsi + jawaban)."""
    ks = []
    nm = _humanize(d.get("intent"))
    if nm:
        ks.append(nm)
    for p in _json_list(d.get("training_phrase_contoh")):
        s = _clip(p, 200)
        if s:
            ks.append(s)
    for f in ("deskripsi_cakupan", "deskripsi_maksud"):
        s = _clip(d.get(f), 240)
        if s:
            ks.append(s)
    ans = _clip(d.get("jawaban_cuplikan"), 240)
    if ans:
        ks.append(ans)
    out, seen = [], set()
    for k in ks:
        kl = k.lower()
        if kl in seen:
            continue
        seen.add(kl)
        out.append(k)
    return out


def _fetch_rows():
    if imdb is None:
        return [], (0, "")
    try:
        c = imdb.init_catalog(imdb.connect())
    except Exception:
        return [], (0, "")
    try:
        try:
            r = c.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM intentmap_catalog"
            ).fetchone()
            sig = (int(r[0]), str(r[1] or ""))
        except Exception:
            sig = (0, "")
        try:
            rows = [dict(x) for x in c.execute(_SQL_CAT).fetchall()]
        except Exception:
            rows = []
        return rows, sig
    finally:
        try:
            c.close()
        except Exception:
            pass


def _build_index():
    global _INDEX
    rows, sig = _fetch_rows()
    if _INDEX is not None and _INDEX.get("sig") == sig:
        return _INDEX
    mk = _maxkeys()
    entries, keys, owner = [], [], []
    for d in rows:
        eks = _keys(d)[:mk]
        if not eks:
            continue
        ei = len(entries)
        entries.append(d)
        for k in eks:
            keys.append(k)
            owner.append(ei)
    emb = _encode(keys) if keys else None
    _INDEX = {
        "sig": sig,
        "entries": entries,
        "emb": emb,
        "owner": (np.asarray(owner) if (np is not None and owner) else owner),
    }
    return _INDEX


def warmup():
    """Bangun indeks lebih awal (mis. saat startup) agar query pertama cepat.
    Aman & gagal-anggun."""
    if not is_available():
        return False
    try:
        idx = _build_index()
        return bool(idx and idx.get("emb") is not None)
    except Exception:
        return False


def rank(query, limit=8, min_score=None):
    """Kembalikan [(entry_dict, skor_cosine)] terurut menurun (>= ambang).
    entry_dict = baris katalog (intent, jawaban_cuplikan, deskripsi_*), siap
    dirender oleh rag_engine._ctx_dialogflow."""
    q = (query or "").strip()
    if not q or not is_available():
        return []
    ms = _min_score() if min_score is None else float(min_score)
    qv = _encode([q])
    if qv is None or len(qv) == 0:
        return []
    qv = qv[0]
    try:
        idx = _build_index()
    except Exception:
        return []
    emb = idx.get("emb")
    entries = idx.get("entries")
    owner = idx.get("owner")
    if emb is None or not entries:
        return []
    try:
        sims = emb @ qv  # emb & qv ter-normalisasi -> cosine
    except Exception:
        return []
    best = {}
    for j in range(len(sims)):
        ei = int(owner[j])
        s = float(sims[j])
        if s > best.get(ei, -1.0):
            best[ei] = s
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    lim = max(1, int(limit or 8))
    out = []
    for ei, s in ranked:
        if s < ms:
            break
        out.append((entries[ei], round(s, 4)))
        if len(out) >= lim:
            break
    return out
