# -*- coding: utf-8 -*-
"""rag_routes.py — Menu RAG (Pilot) "Agent Kring Pajak".

Satu kolom chat yang HANYA menjawab berdasarkan TIGA basis data internal:
  1. Training Phrase & Intent  (intentmap_db: Peta Intent + Katalog, + SBERT)
  2. Percakapan AWE            (avaya_db: transkrip layanan Kring Pajak)
  3. Data Sosmed              (sosmed_db: pasangan Q&A + balasan resmi)

Tidak memakai pengetahuan umum / web. Bila jawaban tidak ada di konteks ->
kalimat fallback. LLM dipanggil via llm_client.chat (provider di .env).

Daftarkan dengan:  import rag_routes; rag_routes.register(app)
"""
import re
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import llm_client
import pii_mask

try:
    import intentmap_db as imdb
except Exception:            # pragma: no cover
    imdb = None
try:
    import avaya_db as avdb
except Exception:            # pragma: no cover
    avdb = None
try:
    import sosmed_db as sdb
except Exception:            # pragma: no cover
    sdb = None
try:
    import knowledge_semantic as ksem
except Exception:            # pragma: no cover
    ksem = None


# ===========================================================================
# ============  SYSTEM PROMPT — UNTUK DIREVIEW (Agent Kring Pajak)  ==========
# Ubah teks di blok ini untuk menyetel persona & pagar jawaban RAG.
# Placeholder {fallback} otomatis diisi dari FALLBACK_ANSWER.
# ===========================================================================
FALLBACK_ANSWER = (
    "Mohon maaf, informasi mengenai hal tersebut belum tersedia pada basis "
    "data kami. Untuk memperoleh jawaban yang lebih pasti, Anda dapat "
    "menghubungi Kring Pajak di 1500200 atau mengunjungi kantor pajak "
    "terdekat. Terima kasih."
)

SYSTEM_PROMPT = (
    "PERAN\n"
    "Kamu adalah Agent Kring Pajak - asisten informasi layanan perpajakan "
    "resmi. Layani wajib pajak dengan ramah, formal, sopan, dan normatif, "
    "layaknya petugas Kring Pajak profesional.\n\n"
    "SUMBER JAWABAN (WAJIB)\n"
    "Jawab HANYA berdasarkan \"KONTEKS INTERNAL\" di bawah, yang bersumber dari "
    "tiga basis data internal: (1) Training Phrase & Intent, (2) Percakapan "
    "AWE, (3) Data Sosmed. DILARANG KERAS memakai pengetahuan umum, asumsi, "
    "ingatan model, atau sumber eksternal/web. Jangan mengarang, menebak, "
    "menambah, atau melengkapi hal yang tidak ada dalam konteks.\n\n"
    "BILA TIDAK ADA DI DATA\n"
    "Jika jawaban tidak ada atau tidak memadai dalam konteks, JANGAN "
    "mengarang. Balas PERSIS dengan kalimat berikut, tanpa tambahan apa pun:\n"
    "\"{fallback}\"\n\n"
    "GAYA & BATASAN\n"
    "- Normatif: bahasa Indonesia baku, formal, sesuai ketentuan; tanpa opini "
    "pribadi.\n"
    "- Netral & tanpa penghakiman: jangan menghakimi, menyalahkan, atau "
    "menggurui; tetap empatik dan objektif.\n"
    "- Tanpa politik/SARA: tolak dengan sopan pertanyaan di luar ranah "
    "perpajakan dan arahkan kembali ke topik perpajakan.\n"
    "- Jangan memberi nasihat hukum/keuangan pribadi di luar data; untuk kasus "
    "spesifik arahkan menghubungi Kring Pajak 1500200 atau kantor pajak bila "
    "konteks mendukung.\n"
    "- Lindungi data pribadi; jangan meminta atau menampilkan data sensitif "
    "tanpa perlu.\n"
    "- Ringkas, jelas, dan langkah demi langkah bila prosedural.\n\n"
    "CARA MEMAKAI KONTEKS\n"
    "Prioritaskan balasan resmi/terverifikasi (AWE dan Sosmed) serta intent "
    "yang paling relevan. Bila konteks saling bertentangan, pilih yang paling "
    "resmi dan mutakhir; bila tetap ragu, gunakan kalimat fallback."
)
# ===========================================================================


_STOP = set("""yang dan di ke dari untuk pada dengan atau ini itu ada apa
bagaimana gimana kenapa mengapa kah min admin kak pak bu mohon tolong ya nya
saya aku kami kita mau ingin bisa tidak gak ga nggak sudah belum juga kalau
jika saja lagi kok dong sih halo hai cara adalah akan tentang the a an is to of
for kalo klo utk yg gmn dll dsb""".split())


def _tokens(text, k=12):
    if not text:
        return []
    out = []
    for w in re.findall(r"[a-z0-9]{3,}", str(text).lower()):
        if w in _STOP or w in out:
            continue
        out.append(w)
        if len(out) >= k:
            break
    return out


def _clip(s, n=600):
    s = (s or "").strip()
    return (s[:n].rstrip() + "\u2026") if len(s) > n else s


def _merge_entries(primary, extra, limit=5):
    out, seen = [], set()
    for x in list(primary or []) + list(extra or []):
        if not isinstance(x, dict):
            continue
        key = x.get("id") or x.get("intent") or id(x)
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# Sumber 1: Training Phrase & Intent (Dialogflow)
# --------------------------------------------------------------------------
def _ctx_dialogflow(q):
    if imdb is None:
        return "", []
    try:
        c = imdb.init_db(imdb.connect())
    except Exception:
        return "", []
    m, mc, t1, t2 = [], [], "", ""
    try:
        try:
            imdb.init_catalog(c)
        except Exception:
            pass
        try:
            m = imdb.match(c, q, limit=4) or []
        except Exception:
            m = []
        try:
            mc = imdb.match_catalog(c, q, limit=4) or []
        except Exception:
            mc = []
        sem = {}
        if ksem is not None:
            try:
                if ksem.is_available():
                    sem = ksem.semantic_match(q, per_lib_limit=3) or {}
            except Exception:
                sem = {}
        m = _merge_entries(m, sem.get("intentmap"))
        mc = _merge_entries(mc, sem.get("katalog"))
        try:
            t1 = imdb.build_context_text(m) if m else ""
        except Exception:
            t1 = ""
        try:
            t2 = imdb.build_catalog_context_text(mc) if mc else ""
        except Exception:
            t2 = ""
    finally:
        try:
            c.close()
        except Exception:
            pass
    sources = []
    for e in list(m or []) + list(mc or []):
        nm = e.get("intent") if isinstance(e, dict) else None
        if nm and not any(s["judul"] == nm for s in sources):
            sources.append({"sumber": "Training Phrase & Intent", "judul": nm, "ref": ""})
    body = "\n\n".join([t for t in (t1, t2) if t and t.strip()])
    return body, sources


# --------------------------------------------------------------------------
# Sumber 2: Percakapan AWE (Avaya)
# --------------------------------------------------------------------------
def _ctx_awe(q, limit=3):
    if avdb is None:
        return "", []
    toks = _tokens(q, k=10)
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
        sql = ("SELECT sid,tanggal,customer,agent_name,mapped_intent,jenis_layanan,"
               "topik,transkrip_json FROM awe_conversations "
               "WHERE transkrip_json IS NOT NULL AND (" + where + ") LIMIT 400")
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
            try:
                is_agent = avdb._is_agent(role, text)
            except Exception:
                is_agent = False
            (agent if is_agent else cust).append(str(text))
        if not agent:
            continue
        label = d.get("jenis_layanan") or d.get("mapped_intent") or d.get("topik") or "Percakapan AWE"
        blocks.append("Topik: %s\nPertanyaan pelanggan: %s\nJawaban petugas: %s"
                      % (label, _clip(" ".join(cust), 300) or "-", _clip(" ".join(agent), 500)))
        sources.append({"sumber": "Percakapan AWE", "judul": str(label),
                        "ref": ("SID " + str(d.get("sid") or "")).strip()})
    return "\n\n".join(blocks), sources


# --------------------------------------------------------------------------
# Sumber 3: Data Sosmed
# --------------------------------------------------------------------------
def _ctx_sosmed(q, limit=3):
    if sdb is None:
        return "", []
    toks = _tokens(q, k=10)
    if not toks:
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
        hay = ((p.get("pertanyaan") or "") + " " + str(p.get("topik") or "")).lower()
        score = sum(hay.count(t) for t in toks)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    blocks, sources = [], []
    for score, p in scored[:limit]:
        label = p.get("topik") or "Data Sosmed"
        blocks.append("Topik: %s\nPertanyaan: %s\nJawaban resmi: %s"
                      % (label, _clip(p.get("pertanyaan"), 300), _clip(p.get("jawaban_draf"), 500)))
        sources.append({"sumber": "Data Sosmed", "judul": str(label),
                        "ref": str(p.get("platform") or "").upper()})
    return "\n\n".join(blocks), sources


def _build_context(q, max_chars=6000):
    parts = []
    t1, s1 = _ctx_dialogflow(q)
    t2, s2 = _ctx_awe(q)
    t3, s3 = _ctx_sosmed(q)
    if t1 and t1.strip():
        parts.append("### Sumber 1 - Training Phrase & Intent\n" + t1)
    if t2 and t2.strip():
        parts.append("### Sumber 2 - Percakapan AWE\n" + t2)
    if t3 and t3.strip():
        parts.append("### Sumber 3 - Data Sosmed\n" + t3)
    sources = s1 + s2 + s3
    body = "\n\n".join(parts)
    if max_chars and len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\u2026"
    return body, sources


def answer_rag(question, history=None):
    q = (question or "").strip()
    if not q:
        return {"ok": False, "error": "Pertanyaan kosong."}
    context, sources = _build_context(q)
    if not context.strip():
        return {"ok": True, "answer": FALLBACK_ANSWER, "sources": [], "grounded": False}
    system = SYSTEM_PROMPT.replace("{fallback}", FALLBACK_ANSWER)
    system += ("\n\n=== KONTEKS INTERNAL ===\n" + context + "\n=== AKHIR KONTEKS INTERNAL ===")
    msgs = []
    for h in (history or [])[-6:]:
        if not isinstance(h, dict):
            continue
        role = (h.get("role") or "").lower()
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": pii_mask.mask_text(content)})
    msgs.append({"role": "user", "content": pii_mask.mask_text(q)})
    try:
        answer = llm_client.chat(msgs, system=pii_mask.mask_text(system),
                                 max_new_tokens=800, temperature=0.2)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "answer": (answer or FALLBACK_ANSWER),
            "sources": sources, "grounded": True}


async def page_rag(request: Request):
    return render_page(request, "rag.html", "rag")


async def api_rag_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    history = body.get("history") if isinstance(body.get("history"), list) else []
    if not question and history:
        for h in reversed(history):
            if isinstance(h, dict) and (h.get("role") or "").lower() == "user":
                question = (h.get("content") or "").strip()
                break
    if not question:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    try:
        res = await run_in_threadpool(answer_rag, question, history)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def register(app):
    app.add_api_route("/rag", page_rag, methods=["GET"])
    app.add_api_route("/api/rag/chat", api_rag_chat, methods=["POST"])
