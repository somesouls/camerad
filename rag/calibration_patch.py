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

Track D langkah 2 (rewrite dua-arah untuk gerbang): gerbang PERATURAN kini
menilai cosine MAKS lintas-varian kueri (rag.rewrite.varian_kueri -> [asli,
baku]) alih-alih hanya query asli. Kueri kolokial/definisi yang normanya ADA di
korpus tetapi cosine query-aslinya < ambang (8 false-abstain A2) terselamatkan
TANPA menurunkan ambang; kueri OOD tak punya padanan baku (varian = [asli] saja)
sehingga tetap tergerbang. Dikendalikan env RAG_GATE_REWRITE (default on).

GAGAL-ANGGUN: bila numpy/model/vektor/modul rewrite tak tersedia, gerbang tidak
menyaring / jatuh ke skor query-asli. Dipasang lewat web_app.py (import) SETELAH
rag_successor_patch.
"""
import os
import rag.engine as _re
import peraturan.db as _pdb
import rag.calibration as _cal


# ---- Rewrite dua-arah untuk gerbang (Track D langkah 2) ----------------------
# Gerbang cosine menilai kemiripan pada query. Untuk kueri kolokial/informal,
# cosine query-asli sering < ambang walau normanya ADA di korpus (false-abstain,
# lihat A2: 8/18 @0,61). rag.rewrite.varian_kueri() menghasilkan [asli, baku]
# (baku = normalisasi istilah pajak, deterministik non-LLM). Gerbang menilai
# cosine MAKS lintas-varian sehingga kueri sah terselamatkan TANPA menurunkan
# ambang; kueri OOD tak punya padanan baku (varian = [asli] saja) -> tetap
# tergerbang (leak tetap 0). GAGAL-ANGGUN bila modul rewrite tak tersedia ->
# jatuh ke skor query-asli.
def _gate_rewrite_enabled():
    v = os.environ.get("RAG_GATE_REWRITE", "1")
    return str(v).strip().lower() not in ("0", "false", "no")


def _varian_kueri(query):
    if not _gate_rewrite_enabled():
        return [query]
    try:
        import rag.rewrite as _rw
        vs = _rw.varian_kueri(query)
        return vs if vs else [query]
    except Exception:
        return [query]


def _skor_maks_varian(query, ids):
    """{id: cosine MAKS lintas-varian kueri} (fail-open kosong)."""
    if not ids:
        return {}
    best = {}
    for v in _varian_kueri(query):
        try:
            skor = _cal.skor_peraturan(v, ids)
        except Exception:
            skor = None
        if not skor:
            continue
        for i, c in skor.items():
            if c is None:
                continue
            if i not in best or c > best[i]:
                best[i] = c
    return best


# ---- (1) Gerbang PERATURAN: bungkus peraturan_db.search ----------------------
_orig_search = _pdb.search


def _search_gated(query, k=10, status_list=("berlaku",), conn=None):
    rows = _orig_search(query, k=k, status_list=status_list, conn=conn)
    try:
        if not _cal.aktif() or not rows:
            return rows
        mc = _cal.get_min_cos()
        ids = [d.get("id") for d in rows if isinstance(d, dict) and d.get("id")]
        skor = _skor_maks_varian(query, ids)
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
      "(min_cos default=%.3f, rewrite-gerbang=%s)"
      % (_cal.default_min_cos(), "on" if _gate_rewrite_enabled() else "off"))
