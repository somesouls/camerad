# -*- coding: utf-8 -*-
"""voicebot/nlu.py -- NLU hybrid LOKAL (embedding + fuzzy fallback).

Klasifikasi intent dari training phrase (vb_intents). Dua mesin, otomatis pilih
yang tersedia (keduanya offline):
  1. Embedding (sentence-transformers) -- cosine similarity, akurasi lebih baik.
  2. Fuzzy (rapidfuzz)                 -- fallback ringan bila embedder tak ada.

classify(text) -> {intent, score(0..1), response, engine}. Fail-soft: skor 0.
Model + indeks di-cache proses; indeks dibangun ulang bila daftar phrase berubah.
"""
import os

from voicebot import config_db as cfg

_EMB = None
_EMB_TRIED = False
_CACHE = {"sig": None, "phrases": [], "vecs": None}


def _emb_model_name():
    return (os.environ.get("VOICEBOT_EMB_MODEL")
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


def _load_embedder():
    global _EMB, _EMB_TRIED
    if _EMB is not None or _EMB_TRIED:
        return _EMB
    _EMB_TRIED = True
    try:
        from sentence_transformers import SentenceTransformer
        _EMB = SentenceTransformer(_emb_model_name())
        print("[voicebot.nlu] embedder siap: %s" % _emb_model_name(), flush=True)
    except Exception as e:  # noqa: BLE001
        print("[voicebot.nlu] embedder tak tersedia (%s); pakai fuzzy." % e, flush=True)
        _EMB = None
    return _EMB


def _signature(phrases):
    try:
        return hash(tuple(p[1].lower() for p in phrases))
    except Exception:
        return None


def _ensure_index(phrases):
    emb = _load_embedder()
    if emb is None:
        return None
    sig = _signature(phrases)
    if _CACHE["sig"] == sig and _CACHE["vecs"] is not None:
        return _CACHE["vecs"]
    texts = [p[1] for p in phrases]
    try:
        vecs = emb.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    except Exception as e:  # noqa: BLE001
        print("[voicebot.nlu] encode gagal: %s" % e, flush=True)
        return None
    _CACHE["sig"] = sig
    _CACHE["phrases"] = phrases
    _CACHE["vecs"] = vecs
    return vecs


def _fuzzy(text, phrases):
    try:
        from rapidfuzz import process, fuzz
        choices = [p[1] for p in phrases]
        m = process.extractOne(text, choices, scorer=fuzz.token_set_ratio)
        if not m:
            return 0.0, -1
        return float(m[1]) / 100.0, int(m[2])
    except Exception:
        # fallback super sederhana: Jaccard token
        ql = set(text.lower().split())
        best, bi = 0.0, -1
        for i, tup in enumerate(phrases):
            pl = set((tup[1] or "").lower().split())
            if not pl:
                continue
            j = len(ql & pl) / float(len(ql | pl) or 1)
            if j > best:
                best, bi = j, i
        return best, bi


def classify(text, conn=None):
    text = (text or "").strip()
    result = {"intent": None, "score": 0.0, "response": "", "engine": "none"}
    if not text:
        return result
    phrases = cfg.all_phrases(conn=conn)
    if not phrases:
        return result
    vecs = _ensure_index(phrases)
    if vecs is not None:
        try:
            import numpy as np
            q = _EMB.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
            sims = vecs @ q
            bi = int(np.argmax(sims))
            score = float(sims[bi])
            name, _ph, resp = phrases[bi]
            result.update({"intent": name, "score": max(0.0, min(1.0, score)),
                           "response": resp, "engine": "embedding"})
            return result
        except Exception as e:  # noqa: BLE001
            print("[voicebot.nlu] similarity gagal: %s" % e, flush=True)
    score, bi = _fuzzy(text, phrases)
    if bi is not None and bi >= 0:
        name, _ph, resp = phrases[bi]
        result.update({"intent": name, "score": float(score), "response": resp,
                       "engine": "fuzzy"})
    return result


def reset_cache():
    _CACHE["sig"] = None
    _CACHE["vecs"] = None
    _CACHE["phrases"] = []
