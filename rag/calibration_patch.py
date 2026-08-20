# -*- coding: utf-8 -*-
"""rag_calibration_patch.py — Point 3: gerbang ambang cosine untuk mesin RAG.

Memasang gerbang keyakinan semantik pada jalur retrieval mesin chat sehingga
bot bisa "diam (abstain)" saat relevansi rendah — dan ambangnya bisa dikalibrasi
objektif lewat menu /rag-eval (sweep). Ambang aktif dibaca dari rag_calibration
(per-thread saat evaluasi; env RAG_MIN_COS untuk produksi).

Dua gerbang (menyaring HANYA saat ambang > 0):
  1. PERATURAN (per-pasal): membungkus peraturan_db.search agar hanya meloloskan
     hasil dengan cosine e5 >= ambang. Berlaku juga untuk pemanggilan dari
     rag_successor_patch (yang memakai pdb.search).
  2. INTENT/knowledge (per-sumber): membungkus rag_engine._ctx_dialogflow agar
     bila cosine SBERT tertinggi < ambang, sumber intent dikosongkan.

GAGAL-ANGGUN: bila numpy/model/vektor tak tersedia, gerbang tidak menyaring.
Dipasang lewat web_app.py (import) SETELAH rag_successor_patch.
"""
import rag_engine as _re
import peraturan_db as _pdb
import rag_calibration as _cal


# ---- (1) Gerbang PERATURAN: bungkus peraturan_db.search ----------------------
_orig_search = _pdb.search


def _search_gated(query, k=10, status_list=("berlaku",), conn=None):
    rows = _orig_search(query, k=k, status_list=status_list, conn=conn)
    try:
        if not _cal.aktif() or not rows:
            return rows
        mc = _cal.get_min_cos()
        ids = [d.get("id") for d in rows if isinstance(d, dict) and d.get("id")]
        skor = _cal.skor_peraturan(query, ids)
        if not skor:
            return rows                 # fail-open: tak bisa menilai -> jangan saring
        keep = []
        for d in rows:
            if not isinstance(d, dict):
                keep.append(d)
                continue
            c = skor.get(d.get("id"))
            if c is None:
                keep.append(d)          # fail-open utk id tanpa vektor
                continue
            d["cos"] = round(c, 4)
            if c >= mc:
                keep.append(d)
        return keep
    except Exception:
        return rows


_pdb.search = _search_gated


# ---- (2) Gerbang INTENT: bungkus rag_engine._ctx_dialogflow ------------------
_orig_ctx_df = _re._ctx_dialogflow


def _ctx_dialogflow_gated(q):
    text, sources = _orig_ctx_df(q)
    try:
        if not _cal.aktif():
            return text, sources
        best = _cal.skor_intent_terbaik(q)
        if best is None:
            return text, sources        # fail-open (model tak tersedia)
        if best < _cal.get_min_cos():
            return "", []               # gerbang tutup -> sumber intent kosong
        return text, sources
    except Exception:
        return text, sources


_re._ctx_dialogflow = _ctx_dialogflow_gated
try:
    _re._DISPATCH["intent"] = _ctx_dialogflow_gated
except Exception:
    pass

print("[rag_calibration_patch] gerbang cosine peraturan + intent aktif "
      "(min_cos default=%.3f)" % _cal.default_min_cos())
