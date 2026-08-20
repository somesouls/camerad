# -*- coding: utf-8 -*-
"""rag_sources_patch.py — Fase 1: pemerataan retrieval untuk SOP / AWE / Sosmed.

Masalah: jalur PERATURAN sudah full-stack (perluasan kamus + AI rewrite, hybrid
FTS5+vektor, rerank cross-encoder), sedangkan sumber lain tertinggal:
  * SOP    : hybrid tapi TANPA perluasan query & TANPA rerank.
  * AWE    : prefilter LIKE + skor hitung-token mentah, tanpa rerank.
  * Sosmed : skor hitung-token mentah pada FAQ, tanpa rerank.

Patch ini (mengikuti konvensi *_patch.py di repo):

  1. SOP — membungkus sop_db.search (seperti rag_rerank_patch membungkus
     peraturan_db.search): query diperluas (kamus sinonim + AI rewrite lewat
     rag_rewrite.untuk_retrieval) lalu kandidat di-rerank cross-encoder memakai
     query ASLI.
  2. AWE — pengganti _DISPATCH["awe"]: mempertahankan logika bot-filter dari
     awe_botfilter_patch (helper-nya dipakai ulang, logika SQL-nya disalin),
     tapi:
       - prefilter SQL memakai token ASLI + bentuk perluasan kamus (bentuk
         mentah cocok untuk LIKE),
       - skor kandidat memakai token TERNORMALISASI (text_norm: lowercase +
         buang stopword + stemming Sastrawi bila terpasang),
       - top-pool dinilai ulang cross-encoder (query asli) sebelum dipotong.
  3. Sosmed — pengganti _DISPATCH["sosmed"] dengan pola yang sama (perluasan
     kamus + skor ternormalisasi + rerank).

Gagal-anggun: bila text_norm / rag_rewrite / rag_reranker / awe_botfilter_patch
tak tersedia atau error, perilaku kembali seperti semula. Env:
RAG_SOURCES_PATCH=0 mematikan seluruh patch ini.

Dipasang lewat web_app.py (import) SETELAH awe_botfilter_patch &
handoff_routing_patch agar membungkus versi terakhir tiap sumber.
"""
import os
import json

import rag.engine as _re

try:
    import rag.rewrite as _rw
except Exception:            # pragma: no cover
    _rw = None
try:
    import rag.reranker as _rr
except Exception:            # pragma: no cover
    _rr = None
try:
    import common.text_norm as _tn
except Exception:            # pragma: no cover
    _tn = None
try:
    import sop.db as _sopdb
except Exception:            # pragma: no cover
    _sopdb = None
try:
    import awe.botfilter_patch as _bf   # helper bot-filter dipakai ulang
except Exception:            # pragma: no cover
    _bf = None


def _on():
    return str(os.environ.get("RAG_SOURCES_PATCH", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _pool_size(k):
    try:
        base = int(os.environ.get("RAG_RERANK_POOL", "30"))
    except Exception:
        base = 30
    return max(int(k or 10), base)


def _rerank_ok():
    try:
        return _rr is not None and _rr.is_available()
    except Exception:
        return False


def _expand_tokens(q, k=10):
    """Kembalikan (toks_raw, toks_norm).

    toks_raw  : token asli (huruf kecil) + bentuk mentah perluasan kamus — cocok
                untuk prefilter LIKE.
    toks_norm : token ternormalisasi via text_norm (stopword dibuang, stemming
                Sastrawi bila ada) — dipakai untuk skor kandidat.
    """
    toks_raw = _re._tokens(q, k=k)
    if _rw is not None:
        try:
            for term in (_rw.expand_kamus(q) or []):
                for t in _re._tokens(term, k=6):
                    if t not in toks_raw:
                        toks_raw.append(t)
        except Exception:
            pass
    if _tn is not None:
        try:
            toks_norm = _tn.norm_tokens(" ".join(toks_raw), k=0)
        except Exception:
            toks_norm = list(toks_raw)
    else:
        toks_norm = list(toks_raw)
    return toks_raw, toks_norm


def _norm_hay(text):
    """Haystack ternormalisasi; fallback lowercase bila text_norm absen."""
    if _tn is None:
        return (text or "").lower()
    try:
        return _tn.normalize(text)
    except Exception:
        return (text or "").lower()


# ======================================================================
# 1. SOP — bungkus sop_db.search (perluasan query + rerank cross-encoder)
# ======================================================================
def _install_sop():
    if _sopdb is None:
        return
    _orig = _sopdb.search

    def _search_sop(query, k=8, status_list=("aktif",), conn=None):
        q = (query or "").strip()
        q_eff = q
        if _rw is not None and q:
            try:
                q_eff = _rw.untuk_retrieval(q) or q
            except Exception:
                q_eff = q
        use_rr = _rerank_ok()
        ambil = _pool_size(k) if use_rr else k
        rows = _orig(q_eff, k=ambil, status_list=status_list, conn=conn)
        if use_rr and rows:
            try:
                rows = _rr.rerank(q, rows, top_k=k)
            except Exception:
                rows = rows[:k]
        else:
            rows = rows[:k]
        return rows

    _sopdb.search = _search_sop


# ======================================================================
# 2. AWE — bot-filter dipertahankan + skor ternormalisasi + rerank
# ======================================================================
def _ctx_awe_v2(q, limit=3):
    avdb = getattr(_re, "avdb", None)
    if avdb is None or _bf is None:
        return "", []
    toks_raw, toks_norm = _expand_tokens(q, k=10)
    if not toks_raw:
        return "", []
    try:
        c = avdb.init_db(avdb.connect())
    except Exception:
        return "", []
    try:
        # Prefilter LIKE memakai token MENTAH (perluasan kamus ikut menambah
        # cakupan). Filter full-bot dipertahankan persis dari awe_botfilter_patch.
        cond = ("(COALESCE(jenis_layanan,'') LIKE ? OR COALESCE(mapped_intent,'') "
                "LIKE ? OR COALESCE(topik,'') LIKE ? OR COALESCE(transkrip_json,'') LIKE ?)")
        where = " OR ".join([cond] * len(toks_raw))
        params = []
        for t in toks_raw:
            params += ["%" + t + "%"] * 4
        sql = ("SELECT sid,tanggal,customer,agent_name,mapped_intent,jenis_layanan,"
               "topik,transkrip_json FROM awe_conversations "
               "WHERE transkrip_json IS NOT NULL "
               "AND COALESCE(agent_name,'') <> '' "
               "AND LOWER(agent_name) NOT LIKE '%chatbot%' "
               "AND LOWER(agent_name) NOT LIKE '%ccai%' "
               "AND LOWER(agent_name) NOT LIKE '%virtual assistant%' "
               "AND LOWER(agent_name) NOT LIKE '%google%' "
               "AND (" + where + ") LIMIT 400")
        rows = c.execute(sql, params).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    scored = []
    for r in rows:
        d = dict(r)
        if _bf._is_bot_agent(d.get("agent_name")):      # jaring pengaman kedua
            continue
        hay = " ".join([str(d.get("jenis_layanan") or ""), str(d.get("mapped_intent") or ""),
                        str(d.get("topik") or ""), str(d.get("transkrip_json") or "")])
        hay_n = _norm_hay(hay)
        score = sum(hay_n.count(t) for t in toks_norm)
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])

    # Bangun blok kandidat (dengan bot-filter per giliran) SEBELUM rerank agar
    # reranker menilai teks yang benar-benar akan dipakai.
    pool_n = _pool_size(limit) if _rerank_ok() else int(limit or 3)
    cand = []
    for score, d in scored[:pool_n]:
        try:
            tx = json.loads(d.get("transkrip_json") or "[]")
        except Exception:
            tx = []
        cust, agent = [], []
        for seg in tx:
            if not isinstance(seg, dict):
                continue
            role = seg.get("role", "")
            text = seg.get("text", "")
            if not text:
                continue
            if _bf._is_bot_turn(role, text):            # buang giliran Bot / CCAI
                continue
            try:
                is_agent = avdb._is_agent(role, text)
            except Exception:
                is_agent = False
            (agent if is_agent else cust).append(str(text))
        if not agent:                                   # tak ada jawaban petugas -> lewati
            continue
        label = d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Percakapan AWE"
        blok = ("Topik: %s\nPertanyaan pelanggan: %s\nJawaban petugas: %s"
                % (label, _re._clip(" ".join(cust), 300) or "-",
                   _re._clip(" ".join(agent), 500)))
        cand.append({"judul": str(label), "isi": blok, "_d": d, "_blok": blok})
    if _rerank_ok() and len(cand) > 1:
        try:
            cand = _rr.rerank(q, cand, top_k=None)
        except Exception:
            pass
    blocks, sources = [], []
    for citem in cand[:int(limit or 3)]:
        d = citem["_d"]
        blocks.append(citem["_blok"])
        sources.append({"sumber": "Percakapan AWE", "judul": citem["judul"],
                        "ref": ("SID " + str(d.get("sid") or "")).strip()})
    return "\n\n".join(blocks), sources


# ======================================================================
# 3. Sosmed — perluasan kamus + skor ternormalisasi + rerank
# ======================================================================
def _ctx_sosmed_v2(q, limit=3):
    sdb = getattr(_re, "sdb", None)
    if sdb is None:
        return "", []
    toks_raw, toks_norm = _expand_tokens(q, k=10)
    if not toks_norm:
        return "", []
    try:
        c = sdb.init_db(sdb.connect())
    except Exception:
        return "", []
    try:
        fp = sdb.faq_pairs(c, only_answered=True, limit=2000)
        pairs = fp.get("pairs") or []
    except Exception:
        pairs = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    scored = []
    for p in pairs:
        jawaban = (p.get("jawaban_draf") or "").strip()
        if not jawaban:
            continue
        hay = ((p.get("pertanyaan") or "") + " " + str(p.get("topik") or ""))
        hay_n = _norm_hay(hay)
        score = sum(hay_n.count(t) for t in toks_norm)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    pool_n = _pool_size(limit) if _rerank_ok() else int(limit or 3)
    cand = []
    for score, p in scored[:pool_n]:
        label = p.get("topik") or "Data Sosmed"
        blok = ("Topik: %s\nPertanyaan: %s\nJawaban resmi: %s"
                % (label, _re._clip(p.get("pertanyaan"), 300),
                   _re._clip(p.get("jawaban_draf"), 500)))
        cand.append({"judul": str(label), "isi": blok, "_p": p, "_blok": blok})
    if _rerank_ok() and len(cand) > 1:
        try:
            cand = _rr.rerank(q, cand, top_k=None)
        except Exception:
            pass
    blocks, sources = [], []
    for citem in cand[:int(limit or 3)]:
        p = citem["_p"]
        blocks.append(citem["_blok"])
        try:
            url = _re._sosmed_url(p)
        except Exception:
            url = str(p.get("permalink") or "")
        sources.append({"sumber": "Data Sosmed", "judul": citem["judul"],
                        "ref": str(p.get("platform") or "").upper(),
                        "url": url})
    return "\n\n".join(blocks), sources


# ======================================================================
def _install():
    if not _on():
        print("[rag_sources_patch] dimatikan (RAG_SOURCES_PATCH=0).", flush=True)
        return
    if getattr(_re, "_sources_patched", False):
        return
    try:
        _install_sop()
    except Exception as e:
        print("[rag_sources_patch] patch SOP gagal:", e, flush=True)
    try:
        if _bf is not None:
            _re._ctx_awe = _ctx_awe_v2
            if isinstance(getattr(_re, "_DISPATCH", None), dict):
                _re._DISPATCH["awe"] = _ctx_awe_v2
    except Exception as e:
        print("[rag_sources_patch] patch AWE gagal:", e, flush=True)
    try:
        _re._ctx_sosmed = _ctx_sosmed_v2
        if isinstance(getattr(_re, "_DISPATCH", None), dict):
            _re._DISPATCH["sosmed"] = _ctx_sosmed_v2
    except Exception as e:
        print("[rag_sources_patch] patch Sosmed gagal:", e, flush=True)
    _re._sources_patched = True
    print("[rag_sources_patch] SOP+AWE+Sosmed: perluasan kamus + skor "
          "ternormalisasi + rerank aktif (norm=%s, rerank=%s)."
          % (_tn is not None, _rerank_ok()), flush=True)


_install()
