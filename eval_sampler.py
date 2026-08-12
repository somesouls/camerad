# -*- coding: utf-8 -*-
"""eval_sampler.py — Kumpulkan sampel pertanyaan uji untuk evaluasi RAG.

Dua sumber (sesuai kesepakatan peluncuran):
  - LIVECHAT (avaya_db.awe_conversations): pertanyaan customer + GOLD = balasan
    agen. Dipakai menilai benar/salah/halusinasi/abstain.
  - CHATBOT (analytics_db.interactions): pertanyaan asli user ke chatbot
    Dialogflow. Tanpa gold (coverage-only) -> fokus grounded/abstain.

Sampling: stratified (round-robin antar strata) + dedup near-duplicate agar
sampel beragam dan tidak menumpuk di 1-2 topik populer.
"""
import re
import json
import random

import eval_db
import avaya_db as avdb
import analytics_db as adb

_STOP = set("yang dan di ke dari untuk pada dengan atau ini itu ada apa bagaimana "
            "gimana kenapa mengapa min admin kak pak bu mohon tolong ya nya saya aku "
            "kami kita mau ingin bisa tidak gak ga nggak sudah belum juga kalau jika "
            "saja lagi kok dong sih halo hai cara adalah akan the a an is to of for".split())

_GREET = re.compile(r"^(halo|hai|hi|hallo|assalamu|selamat\s+(pagi|siang|sore|malam)|"
                    r"pagi|siang|sore|malam|permisi|maaf|terima\s+kasih|makasih|"
                    r"ok|oke|ya|iya|test|tes)\b", re.I)


def _norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _sig(s):
    """Tanda-tangan token-set untuk deteksi near-duplicate."""
    toks = [w for w in _norm(s).split() if len(w) >= 3 and w not in _STOP]
    return " ".join(sorted(set(toks))[:10])


def _is_customer(role):
    return (role or "").strip().lower() in avdb._CUST_ROLES


def _is_greeting(t):
    t = (t or "").strip()
    if len(t) < 8:
        return True
    return bool(_GREET.match(t))


def _extract_qa(transkrip):
    """Dari list [{role,text}] -> (pertanyaan_customer, gold_agen) atau None."""
    if not isinstance(transkrip, list):
        return None
    cust_q = None
    agent_parts = []
    for seg in transkrip:
        if not isinstance(seg, dict):
            continue
        role = seg.get("role", "")
        text = (seg.get("text", "") or "").strip()
        if not text:
            continue
        if _is_customer(role):
            if cust_q is None and not _is_greeting(text):
                cust_q = text
        elif avdb._is_agent(role, text):
            agent_parts.append(text)
    gold = " ".join(agent_parts).strip()
    if not cust_q or not gold or len(gold) < 20:
        return None
    return cust_q, gold


def _stratified(buckets, n, seed=42):
    """Round-robin antar strata (buckets: {label: [item...]}) sampai n."""
    rnd = random.Random(seed)
    order = list(buckets.keys())
    rnd.shuffle(order)
    pools = {}
    for k in order:
        v = list(buckets[k])
        rnd.shuffle(v)
        pools[k] = v
    out = []
    while len(out) < n:
        progressed = False
        for k in order:
            if pools[k]:
                out.append((k, pools[k].pop()))
                progressed = True
                if len(out) >= n:
                    break
        if not progressed:
            break
    return out


def collect_livechat(n=300, seed=42):
    econn = eval_db.init_db(eval_db.connect())
    try:
        c = avdb.init_db(avdb.connect())
    except Exception as e:
        econn.close()
        return {"ok": False, "error": "avaya.db tak terbaca: %s" % e}
    try:
        rows = c.execute(
            "SELECT sid, mapped_intent, jenis_layanan, topik, transkrip_json "
            "FROM awe_conversations WHERE transkrip_json IS NOT NULL"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    seen = set()
    buckets = {}
    for r in rows:
        d = dict(r)
        try:
            tx = json.loads(d.get("transkrip_json") or "[]")
        except Exception:
            continue
        qa = _extract_qa(tx)
        if not qa:
            continue
        q, gold = qa
        sig = _sig(q)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        label = (d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Lainnya")
        buckets.setdefault(str(label), []).append(
            {"q": q, "gold": gold, "ref": d.get("sid") or "", "label": str(label)})
    picked = _stratified(buckets, n, seed)
    added = 0
    for label, it in picked:
        eval_db.upsert_sample(econn, "livechat", it["q"], gold=it["gold"],
                              label=it["label"], sumber_ref=it["ref"], holdout=1)
        added += 1
    econn.commit()
    counts = eval_db.sample_counts(econn)
    econn.close()
    return {"ok": True, "jenis": "livechat", "kandidat": len(seen),
            "strata": len(buckets), "ditambah": added, "counts": counts}


def collect_chatbot(n=200, seed=42):
    econn = eval_db.init_db(eval_db.connect())
    try:
        c = adb.init_db(adb.connect())
    except Exception as e:
        econn.close()
        return {"ok": False, "error": "analytics.db tak terbaca: %s" % e}
    try:
        rows = c.execute(
            "SELECT user_phrase, intent_name, is_fallback FROM interactions "
            "WHERE is_system=0 AND length(COALESCE(user_phrase,''))>=8"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    seen = set()
    buckets = {}
    for r in rows:
        d = dict(r)
        q = (d.get("user_phrase") or "").strip()
        if _is_greeting(q):
            continue
        sig = _sig(q)
        if not sig or sig in seen:
            continue
        seen.add(sig)
        if d.get("is_fallback"):
            label = "(fallback/tak dikenali)"
        else:
            label = d.get("intent_name") or "(lainnya)"
        buckets.setdefault(str(label), []).append({"q": q, "label": str(label)})
    picked = _stratified(buckets, n, seed)
    added = 0
    for label, it in picked:
        eval_db.upsert_sample(econn, "chatbot", it["q"], gold=None,
                              label=it["label"], sumber_ref="", holdout=1)
        added += 1
    econn.commit()
    counts = eval_db.sample_counts(econn)
    econn.close()
    return {"ok": True, "jenis": "chatbot", "kandidat": len(seen),
            "strata": len(buckets), "ditambah": added, "counts": counts}


def collect_all(n_live=300, n_chat=200, seed=42):
    a = collect_livechat(n_live, seed=seed)
    b = collect_chatbot(n_chat, seed=seed)
    return {"ok": bool(a.get("ok") or b.get("ok")), "livechat": a, "chatbot": b}
