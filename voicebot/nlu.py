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
        print("[voicebot.nlu] embed