# -*- coding: utf-8 -*-
"""voicebot/nlu.py -- NLU hybrid LOKAL (embedding + fuzzy fallback).

Klasifikasi intent dari training phrase (vb_intents). Dua mesin, otomatis pilih
yang tersedia (keduanya offline):
  1. Embedding (sentence-transformers) -- cosine similarity, akurasi lebih baik.
  2. Fuzzy (rapidfuzz)                 -- fallback ringan bila embedder tak ada.

classify(text) -> {intent, score(0..1), response, engine}. Fail-soft: skor 0.
top_matches(text, k) -> daftar intent teratas (dipakai RAG voicebot; sumber
tunggal = intent + training phrase). Model + indeks di-cache proses; indeks
dibangun ulang bila daftar phrase berubah.

Bias NLU (#5): bila KATA KUNCI tertentu muncul di transkrip, skor intent terkait
dinaikkan nlu_bias_boost. Pemetaan 'kata kunci => Nama Intent' disimpan di setting
nlu_bias_map (satu aturan per baris / dipisah '|'). Boost diterapkan ke skor tiap
phrase milik intent tsb SEBELUM argmax (classify) & sebelum agregasi (top_matches),
sehingga melengkapi STT prediktif: setelah istilah domain lebih akurat ditranskrip,
routing intent-nya juga condong ke intent yang tepat. Semua fail-soft.
"""
import os
import re

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


# ---------------------------------------------------------------- bias (#5)
def _bias_rules(settings):
    """[(kata_kunci_lower, Nama Intent)] dari setting nlu_bias_map.

    Format tiap aturan: 'kata kunci => Nama Intent' (pemisah utama '=>'; juga
    menerima ':' atau '=' bila '=>' tak ada). Dipisah baris baru atau '|'.
    """
    raw = str((settings or {}).get("nlu_bias_map") or "")
    if not raw.strip():
        return []
    rules = []
    for line in re.split(r"[\n|]+", raw):
        line = line.strip()
        if not line:
            continue
        if "=>" in line:
            term, intent = line.split("=>", 1)
        elif ":" in line:
            term, intent = line.split(":", 1)
        elif "=" in line:
            term, intent = line.split("=", 1)
        else:
            continue
        term = term.strip().lower()
        intent = intent.strip()
        if term and intent:
            rules.append((term, intent))
    return rules


def _word_in(text_l, term_l):
    """True bila term_l muncul di text_l (keduanya sudah lowercase). Untuk term
    satu-kata dipakai batas kata; untuk frasa dipakai substring."""
    if not term_l:
        return False
    if " " in term_l:
        return term_l in text_l
    try:
        pat = r"(?<![0-9a-z\u00c0-\u024f])" + re.escape(term_l) + r"(?![0-9a-z\u00c0-\u024f])"
        return re.search(pat, text_l) is not None
    except Exception:
        return term_l in text_l


def _bias_boosts(text, phrases, settings=None, conn=None):
    """List boost (float) sejajar 'phrases', atau None bila tak ada bias aktif."""
    try:
        if settings is None:
            settings = cfg.get_settings(conn=conn)
    except Exception:
        return None
    if not settings or str(settings.get("nlu_bias_enabled", "1")) == "0":
        return None
    rules = _bias_rules(settings)
    if not rules:
        return None
    tl = " " + (text or "").lower().strip() + " "
    hit = set()
    for term, intent in rules:
        if _word_in(tl, term):
            hit.add(intent.strip().lower())
    if not hit:
        return None
    try:
        boost = float(settings.get("nlu_bias_boost") or 0.08)
    except Exception:
        boost = 0.08
    if boost <= 0:
        return None
    return [boost if ((p[0] or "").strip().lower() in hit) else 0.0 for p in phrases]


def _fuzzy(text, phrases, boosts=None):
    try:
        from rapidfuzz import fuzz
        best, bi = 0.0, -1
        for i, tup in enumerate(phrases):
            sc = float(fuzz.token_set_ratio(text, tup[1] or "")) / 100.0
            if boosts is not None:
                try:
                    sc += float(boosts[i])
                except Exception:
                    pass
            if sc > best:
                best, bi = sc, i
        return best, bi
    except Exception:
        # fallback super sederhana: Jaccard token
        ql = set(text.lower().split())
        best, bi = 0.0, -1
        for i, tup in enumerate(phrases):
            pl = set((tup[1] or "").lower().split())
            if not pl:
                continue
            j = len(ql & pl) / float(len(ql | pl) or 1)
            if boosts is not None:
                try:
                    j += float(boosts[i])
                except Exception:
                    pass
            if j > best:
                best, bi = j, i
        return best, bi


def classify(text, conn=None, settings=None):
    text = (text or "").strip()
    result = {"intent": None, "score": 0.0, "response": "", "engine": "none"}
    if not text:
        return result
    phrases = cfg.all_phrases(conn=conn)
    if not phrases:
        return result
    try:
        boosts = _bias_boosts(text, phrases, settings, conn=conn)
    except Exception:
        boosts = None
    vecs = _ensure_index(phrases)
    if vecs is not None:
        try:
            import numpy as np
            q = _EMB.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
            sims = vecs @ q
            if boosts is not None:
                try:
                    sims = sims + np.asarray(boosts, dtype=float)
                except Exception:
                    pass
            bi = int(np.argmax(sims))
            score = float(sims[bi])
            name, _ph, resp = phrases[bi]
            result.update({"intent": name, "score": max(0.0, min(1.0, score)),
                           "response": resp, "engine": "embedding"})
            return result
        except Exception as e:  # noqa: BLE001
            print("[voicebot.nlu] similarity gagal: %s" % e, flush=True)
    score, bi = _fuzzy(text, phrases, boosts)
    if bi is not None and bi >= 0:
        name, _ph, resp = phrases[bi]
        result.update({"intent": name, "score": max(0.0, min(1.0, float(score))),
                       "response": resp, "engine": "fuzzy"})
    return result


def top_matches(text, k=5, conn=None, per_intent_phrases=3, settings=None):
    """Ambil k intent paling relevan untuk sebuah ucapan.

    Dipakai oleh \"RAG voicebot\" (voicebot/rag.py) yang HANYA memakai intent +
    training phrase sebagai sumber. Kembalikan list terurut skor menurun:
        [{\"intent\", \"score\"(0..1), \"response\", \"phrases\": [contoh, ...]}]
    Skor per-intent = kemiripan tertinggi di antara training phrase-nya (+ bias #5).
    Fail-soft: kembalikan [] bila tak ada apa pun.
    """
    text = (text or "").strip()
    if not text:
        return []
    phrases = cfg.all_phrases(conn=conn)
    if not phrases:
        return []
    try:
        boosts = _bias_boosts(text, phrases, settings, conn=conn)
    except Exception:
        boosts = None
    scored = []  # (name, phrase, response, sim)
    vecs = _ensure_index(phrases)
    if vecs is not None:
        try:
            import numpy as np
            q = _EMB.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
            sims = vecs @ q
            for i, tup in enumerate(phrases):
                name, ph, resp = tup
                sc = float(sims[i])
                if boosts is not None:
                    try:
                        sc += float(boosts[i])
                    except Exception:
                        pass
                scored.append((name, ph, resp, sc))
        except Exception as e:  # noqa: BLE001
            print("[voicebot.nlu] top_matches similarity gagal: %s" % e, flush=True)
            scored = []
    if not scored:
        try:
            from rapidfuzz import fuzz
            for i, tup in enumerate(phrases):
                name, ph, resp = tup
                sc = float(fuzz.token_set_ratio(text, ph or "")) / 100.0
                if boosts is not None:
                    try:
                        sc += float(boosts[i])
                    except Exception:
                        pass
                scored.append((name, ph, resp, sc))
        except Exception:
            ql = set(text.lower().split())
            for i, tup in enumerate(phrases):
                name, ph, resp = tup
                pl = set((ph or "").lower().split())
                j = len(ql & pl) / float(len(ql | pl) or 1) if pl else 0.0
                if boosts is not None:
                    try:
                        j += float(boosts[i])
                    except Exception:
                        pass
                scored.append((name, ph, resp, j))
    agg = {}
    for name, ph, resp, sc in scored:
        d = agg.get(name)
        if d is None:
            d = {"intent": name, "score": sc, "response": resp, "_phrases": []}
            agg[name] = d
        if sc > d["score"]:
            d["score"] = sc
        if not d.get("response") and resp:
            d["response"] = resp
        if ph:
            d["_phrases"].append((sc, ph))
    out = []
    for d in agg.values():
        phs = [p for _, p in sorted(d["_phrases"], key=lambda x: x[0], reverse=True)[:per_intent_phrases]]
        out.append({"intent": d["intent"],
                    "score": max(0.0, min(1.0, float(d["score"]))),
                    "response": d.get("response") or "",
                    "phrases": phs})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:k]


def reset_cache():
    _CACHE["sig"] = None
    _CACHE["vecs"] = None
    _CACHE["phrases"] = []
