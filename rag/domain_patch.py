# -*- coding: utf-8 -*-
"""rag_domain_patch.py — Fase 2: sinyal domain hukum ikut menentukan ranking.

Setelah retrieval hybrid (FTS5+vektor, RRF) dan rerank cross-encoder, urutan
akhir disesuaikan dengan sinyal domain yang SUDAH tersimpan di schema namun
sebelumnya tidak dipakai:

  * authority : kekuatan_hukum (UU=100 ... SE=40); can_cite=0 -> penalti.
  * recency   : tahun dokumen (dokumen lebih baru sedikit diunggulkan).
  * entitas   : irisan antara entitas pajak yang terdeteksi pada QUERY (lewat
                kamus_sinonim) dengan kolom `entitas` unit (hasil tagging Fase 2).
  * definisi  : query berpola definisi ("apa itu", "pengertian", "yang dimaksud")
                -> unit yang memuat "yang dimaksud" atau Pasal 1 dinaikkan.
  * temporal  : query menyebut tahun ("... tahun 2019") -> unit difilter as-of
                (valid_from <= tgl <= valid_to; NULL fail-open).

Skor akhir = (1-alpha)*base_ternormalisasi + alpha*domain_score.
  * base  : rerank_skor bila ada, else skor RRF — dinormalisasi min-max dalam
            satu hasil, jadi aman untuk skala skor apa pun.
  * alpha : env RAG_DOMAIN_ALPHA (default 0.25).

Env:
  RAG_DOMAIN_BOOST=0   mematikan seluruh patch.
  RAG_DOMAIN_ALPHA     bobot pencampuran (default 0.25).
  RAG_W_AUTH / RAG_W_RECENCY / RAG_W_ENT / RAG_W_DEF   bobot komponen
                    (default 0.45 / 0.15 / 0.30 / 0.10).

Gagal-anggun penuh: bila apa pun error, hasil dari search bawaan dikembalikan.
Dipasang lewat web_app.py (import) SETELAH rag_calibration_patch agar membungkus
rantai terakhir peraturan_db.search (gate -> rerank -> hybrid).
"""
import os
import re
import json
import time

import peraturan_db as _pdb

try:
    import rag_kamus_db as _kdb
except Exception:            # pragma: no cover
    _kdb = None

_orig_search = _pdb.search

_RE_TAHUN = re.compile(r"\btahun\s+((?:19|20)\d{2})\b", re.I)
_RE_DEF = re.compile(r"(apa itu|apakah yang dimaksud|yang dimaksud|pengertian|definisi|\barti\b)", re.I)


def _on():
    return str(os.environ.get("RAG_DOMAIN_BOOST", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _alpha():
    try:
        v = float(os.environ.get("RAG_DOMAIN_ALPHA", "0.25"))
    except Exception:
        v = 0.25
    return min(max(v, 0.0), 0.9)


def _w(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return default


def _has(text_low, term):
    t = (term or "").strip().lower()
    if not t:
        return False
    return re.search(r"(?<![0-9a-z])" + re.escape(t) + r"(?![0-9a-z])", text_low) is not None


def _json_list(v):
    try:
        x = json.loads(v) if v else []
        return x if isinstance(x, list) else []
    except Exception:
        return []


def _detect_entities(q_low):
    """Himpunan istilah kamus yang bentuknya muncul pada query."""
    det = set()
    if _kdb is None:
        return det
    try:
        entries = _kdb.all_active() or []
    except Exception:
        entries = []
    for e in entries:
        ist = str(e.get("istilah") or "").strip()
        if not ist:
            continue
        for f in (e.get("forms") or []):
            if _has(q_low, f):
                det.add(ist)
                break
    return det


def _as_of(q):
    """Query bertahun ('... tahun 2019') -> tanggal as-of pertengahan tahun."""
    m = _RE_TAHUN.search(q or "")
    if not m:
        return None
    return "%s-07-01" % m.group(1)


def _valid_on(d, as_of):
    vf = str(d.get("valid_from") or "").strip()
    vt = str(d.get("valid_to") or "").strip()
    if vf and vf > as_of:
        return False
    if vt and vt < as_of:
        return False
    return True


def _domain_score(d, ent_detect, is_def):
    try:
        kh = float(d.get("kekuatan_hukum") or 50)
    except Exception:
        kh = 50.0
    authority = max(0.0, min(1.0, kh / 100.0))
    try:
        if int(d.get("can_cite") if d.get("can_cite") is not None else 1) == 0:
            authority *= 0.5
    except Exception:
        pass
    now_y = float(time.localtime().tm_year)
    try:
        th = float(d.get("tahun") or 0)
    except Exception:
        th = 0.0
    recency = 0.0 if th <= 0 else max(0.0, min(1.0, (th - 1980.0) / max(1.0, now_y - 1980.0)))
    ent = 0.0
    if ent_detect:
        ents = set(str(x) for x in _json_list(d.get("entitas")))
        if ents & ent_detect:
            ent = 1.0
    definisi = 0.0
    if is_def:
        isi = str(d.get("isi") or "").lower()
        pasal = str(d.get("pasal") or "").strip()
        if "yang dimaksud" in isi:
            definisi = 1.0
        elif pasal == "1":
            definisi = 0.6
    return (_w("RAG_W_AUTH", 0.45) * authority
            + _w("RAG_W_RECENCY", 0.15) * recency
            + _w("RAG_W_ENT", 0.30) * ent
            + _w("RAG_W_DEF", 0.10) * definisi)


def _search_domain(query, k=10, status_list=("berlaku",), conn=None):
    q = (query or "").strip()
    if not q or not _on():
        return _orig_search(query, k=k, status_list=status_list, conn=conn)
    as_of = _as_of(q)
    # Filter temporal membuang kandidat -> tarik pool lebih besar dulu.
    ambil = max(int(k or 10) * 3, 30) if as_of else k
    try:
        rows = _orig_search(q, k=ambil, status_list=status_list, conn=conn) or []
    except Exception:
        return _orig_search(query, k=k, status_list=status_list, conn=conn)
    if as_of and rows:
        f = [r for r in rows if isinstance(r, dict) and _valid_on(r, as_of)]
        if f:                       # gagal-anggun: bila filter mengosongkan, pakai apa adanya
            rows = f
    if not rows:
        return rows
    ent_detect = _detect_entities(q.lower())
    is_def = bool(_RE_DEF.search(q.lower()))
    bases = []
    for r in rows:
        try:
            b = float(r.get("rerank_skor") if r.get("rerank_skor") is not None else r.get("skor") or 0.0)
        except Exception:
            b = 0.0
        bases.append(b)
    lo, hi = (min(bases), max(bases)) if bases else (0.0, 0.0)
    span = (hi - lo) or 1.0
    alpha = _alpha()
    out = []
    for r, b in zip(rows, bases):
        base_n = (b - lo) / span
        dom = _domain_score(r, ent_detect, is_def)
        final = (1.0 - alpha) * base_n + alpha * dom
        try:
            r["domain_skor"] = round(dom, 4)
            r["skor_akhir"] = round(final, 6)
        except Exception:
            pass
        out.append((final, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:int(k or 10)]]


if _on():
    _pdb.search = _search_domain
    print("[rag_domain_patch] domain boost aktif (alpha=%.2f; "
          "authority/recency/entitas/definisi + filter temporal)." % _alpha(),
          flush=True)
else:
    print("[rag_domain_patch] dimatikan (RAG_DOMAIN_BOOST=0).", flush=True)
