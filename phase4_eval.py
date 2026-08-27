# -*- coding: utf-8 -*-
"""phase4_eval.py — Evaluasi retrieval atas GOLDEN SET (Fase 4), tanpa LLM.

Mengukur kualitas RETRIEVAL (bukan jawaban) secara deterministik:

  * recall@k : fraksi query 'hit' yang rujukan harapannya muncul di top-k.
  * MRR      : Mean Reciprocal Rank posisi rujukan harapan.
  * Proksi abstain: untuk query 'abstain', dilaporkan cosine teratas hasil
    retrieval (via rag.calibration.skor_peraturan) + penanda 'berisiko lolos
    gerbang' bila >= RAG_MIN_COS aktif. (Penilaian abstain END-TO-END tetap
    lewat /rag-eval jenis=golden — skrip ini sengaja tidak memanggil LLM agar
    murah & bisa jadi gerbang cepat.)

Rantai yang diukur = rantai produksi: patch diimpor dengan urutan yang sama
seperti web_app.py (successor -> rerank -> kalibrasi -> domain).

Pemakaian:
  python phase4_eval.py --seed                          # isi golden set + cermin ke /rag-eval
  python phase4_eval.py                                 # jalankan evaluasi retrieval
  python phase4_eval.py --k 10                          # recall@10
  python phase4_eval.py --baseline-save golden_base.json
  python phase4_eval.py --baseline-check golden_base.json [--tolerance 0.05]
  python phase4_eval.py --mine                          # kandidat golden dari feedback produksi

Gerbang upgrade: simpan baseline SESUDAH perubahan tervalidasi; sebelum upgrade
berikutnya jalankan --baseline-check — exit code 1 bila recall/MRR turun
melebihi toleransi.
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

# Muat .env (bila ada) agar env RAG_* / model ikut.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Impor patch retrieval dengan URUTAN sama seperti web_app.py agar yang diukur
# adalah rantai produksi (successor -> rerank -> kalibrasi -> domain).
for _m in ("rag.successor_patch", "rag.rerank_patch",
           "rag.calibration_patch", "rag.domain_patch"):
    try:
        __import__(_m)
    except Exception as _e:  # fail-soft: lanjut tanpa patch tsb
        print("[phase4_eval] impor %s dilewati: %s" % (_m, _e), flush=True)

import peraturan.db as pdb
import rag.golden_db as gdb

try:
    import rag.calibration as _cal
except Exception:            # pragma: no cover
    _cal = None


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


# ------------------------------------------------------------------ matching
def _norm_key(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _row_text(d):
    return " ".join(str(d.get(k) or "")
                    for k in ("judul", "hierarchy", "isi")).lower()


def _match_rank(rows, expect):
    """Rank 1-based baris pertama yang cocok ekspektasi; 0 bila tak ada.

    Cocok bila: salah satu nomor harapan cocok (substring dua arah setelah
    dinormalisasi) ATAU semua keywords muncul pada SATU baris.
    """
    nomors = [_norm_key(x) for x in (expect.get("nomor") or []) if str(x).strip()]
    nomors = [n for n in nomors if n]
    kws = [str(x).lower().strip() for x in (expect.get("keywords") or []) if str(x).strip()]
    for i, r in enumerate(rows, start=1):
        rn = _norm_key(r.get("nomor"))
        hit_nomor = False
        if rn:
            for en in nomors:
                if en in rn or rn in en:
                    hit_nomor = True
                    break
        txt = _row_text(r)
        hit_kw = bool(kws) and all(kw in txt for kw in kws)
        if hit_nomor or hit_kw:
            return i
    return 0


def _top1_label(rows):
    if not rows:
        return ""
    r0 = rows[0]
    parts = [str(r0.get("jenis_peraturan") or ""), str(r0.get("nomor") or "")]
    lab = " ".join(p for p in parts if p).strip()
    if r0.get("pasal"):
        lab += " - Pasal %s" % r0["pasal"]
    return lab


#