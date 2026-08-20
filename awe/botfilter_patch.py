# -*- coding: utf-8 -*-
"""awe_botfilter_patch.py — Bersihkan knowledge AWE dari giliran Bot / CCAI.

Masalah: retrieval "Percakapan AWE" (rag_engine._ctx_awe) dulu memisahkan
transkrip hanya menjadi agent vs non-agent, sehingga SEMUA giliran Bot / CCAI
(mis. pesan sambutan "Selamat datang di layanan Virtual Assistant ...") jatuh ke
keranjang "pelanggan" dan ikut jadi konteks. Akibatnya mesin RAG kerap
mencocokkan kata kunci ke boilerplate chatbot, bukan ke jawaban petugas.

Perbaikan (tanpa menyentuh data — murni saat retrieval):
  1. ABAIKAN percakapan full-bot: baris awe_conversations yang kolom agent_name-
     nya penanda bot ("Chatbot, Google", CCAI, Virtual Assistant, dsb) atau
     kosong -> tidak pernah diambil sebagai knowledge.
  2. BUANG giliran Bot / CCAI dari transkrip: hanya giliran PELANGGAN + AGENT
     MANUSIA yang dirangkai menjadi "Pertanyaan pelanggan" / "Jawaban petugas".

Mengikuti pola monkey-patch rag_successor_patch / rag_grounding_patch: mengganti
rag_engine._ctx_awe DAN entri rag_engine._DISPATCH["awe"] (yang menyimpan
referensi fungsi lama). Fail-safe: bila rag_engine/avaya_db tak tersedia, tidak
melakukan apa-apa.
"""
import re
import json

import rag.engine as _re

# Penanda peran/agent bot (selaras dengan avaya_db).
_CUST_ROLES = {"customer", "cust", "pelanggan", "user"}
_BOT_ROLES = {"bot", "ccai", "chatbot", "virtual assistant"}
_BOT_ROLE_RE = re.compile(r"\bbot\b|ccai|chatbot|virtual\s+assistant|google", re.I)
_BOT_PHRASE = re.compile(
    r"virtual\s+assistant\s+\(chat\s+bot\)|petugas\s+kami\s+akan\s+segera\s+membantu",
    re.I)
_BOT_AGENT_RE = re.compile(r"chatbot|ccai|virtual\s+assistant|google", re.I)


def _is_bot_agent(name):
    """True bila kolom agent_name menandakan percakapan full-bot (atau kosong)."""
    n = (name or "").strip()
    if not n:
        return True
    return bool(_BOT_AGENT_RE.search(n))


def _is_bot_turn(role, text):
    """True bila satu giliran transkrip berasal dari Bot / CCAI."""
    r = (role or "").strip().lower()
    if r in _CUST_ROLES:
        return False
    if r in _BOT_ROLES or _BOT_ROLE_RE.search(r):
        return True
    if _BOT_PHRASE.search(text or ""):
        return True
    return False


def _ctx_awe(q, limit=3):
    avdb = getattr(_re, "avdb", None)
    if avdb is None:
        return "", []
    toks = _re._tokens(q, k=10)
    if not toks:
        return "", []
    try:
        c = avdb.init_db(avdb.connect())
    except Exception:
        return "", []
    try:
        cond = ("(COALESCE(jenis_layanan,'') LIKE ? OR COALESCE(mapped_intent,'') "
                "LIKE ? OR COALESCE(topik,'') LIKE ? OR COALESCE(transkrip_json,'') LIKE ?)")
        where = " OR ".join([cond] * len(toks))
        params = []
        for t in toks:
            params += ["%" + t + "%"] * 4
        # Saring percakapan full-bot langsung di SQL: kolom Agent tidak boleh
        # kosong maupun penanda bot (mis. "Chatbot, Google").
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
        if _is_bot_agent(d.get("agent_name")):      # jaring pengaman kedua
            continue
        hay = " ".join([str(d.get("jenis_layanan") or ""), str(d.get("mapped_intent") or ""),
                        str(d.get("topik") or ""), str(d.get("transkrip_json") or "")]).lower()
        score = sum(hay.count(t) for t in toks)
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    blocks, sources = [], []
    for score, d in scored[:limit]:
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
            if _is_bot_turn(role, text):            # buang giliran Bot / CCAI
                continue
            try:
                is_agent = avdb._is_agent(role, text)
            except Exception:
                is_agent = False
            (agent if is_agent else cust).append(str(text))
        if not agent:                               # tak ada jawaban petugas -> lewati
            continue
        label = d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Percakapan AWE"
        blocks.append("Topik: %s\nPertanyaan pelanggan: %s\nJawaban petugas: %s"
                      % (label, _re._clip(" ".join(cust), 300) or "-",
                         _re._clip(" ".join(agent), 500)))
        sources.append({"sumber": "Percakapan AWE", "judul": str(label),
                        "ref": ("SID " + str(d.get("sid") or "")).strip()})
    return "\n\n".join(blocks), sources


def _install():
    if getattr(_re, "_awe_botfilter_patched", False):
        return
    try:
        _re._ctx_awe = _ctx_awe
        if isinstance(getattr(_re, "_DISPATCH", None), dict):
            _re._DISPATCH["awe"] = _ctx_awe
        _re._awe_botfilter_patched = True
        print("[awe_botfilter_patch] retrieval AWE kini membuang Bot/CCAI & percakapan full-bot.",
              flush=True)
    except Exception as e:        # fail-safe
        print("[awe_botfilter_patch] gagal memasang patch:", e, flush=True)


_install()
