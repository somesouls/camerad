# -*- coding: utf-8 -*-
"""rag_routes.py — Menu RAG (Pilot) "Agent Kring Pajak".

Satu kolom chat yang HANYA menjawab berdasarkan basis data internal:
  1. Training Phrase & Intent  (intentmap_db: Katalog Intent -> jawaban resmi
                                'jawaban_cuplikan' + Peta Intent sebagai panduan)
  2. Percakapan AWE            (avaya_db: transkrip layanan Kring Pajak)
  3. Data Sosmed              (sosmed_db: pasangan Q&A + balasan resmi)
  4. Peraturan               (peraturan_db: basis data peraturan perpajakan;
                                pencarian hybrid FTS5 + semantik e5)
  5. SOP & Proses Bisnis     (sop_db: dokumen SOP/proses bisnis hasil ekstraksi
                                pdf/pptx/docx/txt/html; hybrid FTS5 + e5)

Tidak memakai pengetahuan umum / web. Bila tidak ada konteks relevan ->
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
    import peraturan_db as pdb
except Exception:            # pragma: no cover
    pdb = None
try:
    import sop_db as sopdb
except Exception:            # pragma: no cover
    sopdb = None


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
    "resmi. Bantu wajib pajak dengan bahasa Indonesia yang ramah, formal, "
    "sopan, dan normatif, layaknya petugas Kring Pajak profesional.\n\n"
    "TUGAS\n"
    "Jawab pertanyaan pengguna dengan MEMANFAATKAN \"KONTEKS INTERNAL\" di "
    "bawah. Konteks berisi jawaban resmi intent, cuplikan jawaban petugas "
    "(Percakapan AWE), balasan resmi media sosial (Data Sosmed), kutipan "
    "peraturan perpajakan (Peraturan), serta prosedur baku dan alur proses "
    "bisnis (SOP & Proses Bisnis). Rangkai jawaban yang jelas dan runut "
    "dari informasi yang relevan pada konteks - kamu boleh menyarikan, "
    "menggabungkan, dan merapikan kalimat selama tidak mengubah maksud atau "
    "menambah fakta baru. Bila menyampaikan ketentuan hukum, sebutkan dasar "
    "peraturannya (mis. jenis, nomor, dan pasal) sesuai yang tertera pada "
    "konteks Peraturan; jangan mengarang nomor atau pasal. Bila menjelaskan "
    "prosedur atau alur, ikuti langkah-langkah sesuai konteks SOP & Proses "
    "Bisnis tanpa menambah langkah yang tidak tercantum.\n\n"
    "SUMBER JAWABAN (WAJIB)\n"
    "Gunakan HANYA informasi dari konteks internal. DILARANG memakai "
    "pengetahuan umum, sumber eksternal/web, atau mengarang fakta, angka, "
    "tautan, maupun prosedur yang tidak ada di konteks. Jika konteks hanya "
    "memuat garis besar/cakupan, sampaikan garis besar itu dengan jujur lalu "
    "arahkan menghubungi Kring Pajak 1500200 untuk detail lebih lanjut.\n\n"
    "BILA TIDAK ADA DI DATA\n"
    "HANYA jika di konteks TIDAK ADA satu pun informasi yang relevan dengan "
    "pertanyaan, balas PERSIS dengan kalimat berikut tanpa tambahan apa pun:\n"
    "\"{fallback}\"\n"
    "Jangan gunakan kalimat fallback bila ada informasi relevan pada konteks, "
    "walau hanya sebagian.\n\n"
    "GAYA & BATASAN\n"
    "- Normatif: bahasa Indonesia baku dan formal; tanpa opini pribadi.\n"
    "- Netral & tanpa penghakiman: jangan menghakimi atau menggurui; empatik "
    "dan objektif.\n"
    "- Tanpa politik/SARA: tolak dengan sopan pertanyaan di luar ranah "
    "perpajakan dan arahkan kembali ke topik perpajakan.\n"
    "- Lindungi data pribadi; jangan meminta atau menampilkan data sensitif "
    "tanpa perlu.\n"
    "- Ringkas, jelas, dan sajikan langkah demi langkah bila prosedural.\n"
    "- Prioritaskan jawaban resmi/terverifikasi; bila konteks saling "
    "bertentangan, pilih yang paling resmi dan mutakhir."
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


def _json_list(v):
    try:
        x = json.loads(v) if v else []
        return x if isinstance(x, list) else []
    except Exception:
        return []


# --------------------------------------------------------------------------
# Sumber 1: Training Phrase & Intent (Katalog jawaban resmi + Peta Intent)
# --------------------------------------------------------------------------
def _ctx_dialogflow(q):
    if imdb is None:
        return "", []
    try:
        c = imdb.init_db(imdb.connect())
    except Exception:
        return "", []
    blocks, sources = [], []
    toks = _tokens(q, k=12)
    try:
        try:
            imdb.init_catalog(c)
        except Exception:
            pass
        # (1) Jawaban resmi dari Katalog Intent (kolom jawaban_cuplikan).
        cat_rows = []
        if toks:
            try:
                cat_rows = c.execute(
                    "SELECT intent, deskripsi_maksud, deskripsi_cakupan, "
                    "jawaban_cuplikan, training_phrase_contoh FROM intentmap_catalog "
                    "WHERE COALESCE(sumber_status,'aktif')!='hilang' "
                    "AND COALESCE(soft_deleted,0)=0"
                ).fetchall()
            except Exception:
                cat_rows = []
        scored = []
        for r in cat_rows:
            d = dict(r)
            tps = _json_list(d.get("training_phrase_contoh"))
            iname = str(d.get("intent") or "")
            hay = " ".join([
                iname, str(d.get("deskripsi_maksud") or ""),
                str(d.get("deskripsi_cakupan") or ""),
                str(d.get("jawaban_cuplikan") or ""),
                " ".join(str(x) for x in tps),
            ]).lower()
            score = sum(hay.count(t) for t in toks)
            score += 2 * sum(1 for t in toks if t in iname.lower())
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: -x[0])
        for score, d in scored[:4]:
            intent = str(d.get("intent") or "")
            ans = (d.get("jawaban_cuplikan") or "").strip()
            desc = (d.get("deskripsi_cakupan") or d.get("deskripsi_maksud") or "").strip()
            piece = "Intent: " + intent
            if ans:
                piece += "\nJawaban resmi: " + _clip(ans, 700)
            elif desc:
                piece += "\nCakupan/keterangan: " + _clip(desc, 500)
            else:
                continue
            blocks.append(piece)
            sources.append({"sumber": "Training Phrase & Intent", "judul": intent, "ref": ""})
        # (2) Kebijakan pemetaan analis (Peta Intent) sebagai panduan tambahan.
        try:
            m = imdb.match(c, q, limit=3) or []
        except Exception:
            m = []
        if m:
            try:
                t_pol = imdb.build_context_text(m)
            except Exception:
                t_pol = ""
            if t_pol and t_pol.strip():
                blocks.append(t_pol)
                for e in m:
                    nm = e.get("intent") if isinstance(e, dict) else None
                    if nm and not any(s["judul"] == nm for s in sources):
                        sources.append({"sumber": "Training Phrase & Intent", "judul": nm, "ref": ""})
    finally:
        try:
            c.close()
        except Exception:
            pass
    return "\n\n".join(blocks), sources


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


# --------------------------------------------------------------------------
# Sumber 4: Peraturan (basis data peraturan perpajakan)
# --------------------------------------------------------------------------
def _ctx_peraturan(q, limit=4):
    if pdb is None:
        return "", []
    try:
        # Pencarian hybrid (FTS5 + semantik e5); hanya peraturan berstatus 'berlaku'.
        rows = pdb.search(q, limit, ("berlaku",))
    except Exception:
        rows = []
    blocks, sources = [], []
    for r in rows:
        try:
            d = r if isinstance(r, dict) else dict(r)
        except Exception:
            continue
        isi = str(d.get("isi") or "").strip()
        if not isi:
            continue
        jenis = str(d.get("jenis_peraturan") or "").strip()
        nomor = str(d.get("nomor") or "").strip()
        tahun = str(d.get("tahun") or "").strip()
        pasal = str(d.get("pasal") or "").strip()
        judul = str(d.get("judul") or "").strip()
        hierarchy = str(d.get("hierarchy") or "").strip()
        reference = str(d.get("reference") or "").strip()
        tajuk = " ".join(x for x in [jenis, nomor,
                                     ("Tahun " + tahun) if tahun else ""] if x).strip()
        head = tajuk or judul or "Peraturan"
        if pasal:
            head += " - Pasal " + pasal
        piece = "Peraturan: " + head
        if judul and judul.lower() not in head.lower():
            piece += "\nTentang: " + _clip(judul, 200)
        piece += "\nIsi: " + _clip(isi, 700)
        blocks.append(piece)
        sources.append({"sumber": "Peraturan", "judul": head,
                        "ref": (reference or hierarchy)})
    return "\n\n".join(blocks), sources


# --------------------------------------------------------------------------
# Sumber 5: SOP & Proses Bisnis (dokumen prosedur hasil ekstraksi)
# --------------------------------------------------------------------------
def _ctx_sop(q, limit=4):
    if sopdb is None:
        return "", []
    try:
        # Pencarian hybrid (FTS5 + semantik e5); hanya unit berstatus 'aktif'.
        rows = sopdb.search(q, limit, ("aktif",))
    except Exception:
        rows = []
    blocks, sources = [], []
    for r in rows:
        try:
            d = r if isinstance(r, dict) else dict(r)
        except Exception:
            continue
        isi = str(d.get("isi") or "").strip()
        if not isi:
            continue
        judul = str(d.get("judul") or "").strip()
        kategori = str(d.get("kategori") or "").strip()
        bagian = str(d.get("bagian") or "").strip()
        head = judul or "SOP"
        if kategori:
            head = "%s (%s)" % (head, kategori)
        piece = "Dokumen: " + head
        if bagian:
            piece += "\nBagian: " + _clip(bagian, 160)
        piece += "\nIsi: " + _clip(isi, 700)
        blocks.append(piece)
        sources.append({"sumber": "SOP & Proses Bisnis", "judul": head,
                        "ref": str(d.get("source_file") or "")})
    return "\n\n".join(blocks), sources


def _build_context(q, max_chars=6500):
    parts = []
    t1, s1 = _ctx_dialogflow(q)
    t2, s2 = _ctx_awe(q)
    t3, s3 = _ctx_sosmed(q)
    t4, s4 = _ctx_peraturan(q)
    t5, s5 = _ctx_sop(q)
    if t1 and t1.strip():
        parts.append("### Sumber 1 - Training Phrase & Intent\n" + t1)
    if t2 and t2.strip():
        parts.append("### Sumber 2 - Percakapan AWE\n" + t2)
    if t3 and t3.strip():
        parts.append("### Sumber 3 - Data Sosmed\n" + t3)
    if t4 and t4.strip():
        parts.append("### Sumber 4 - Peraturan\n" + t4)
    if t5 and t5.strip():
        parts.append("### Sumber 5 - SOP & Proses Bisnis\n" + t5)
    sources = s1 + s2 + s3 + s4 + s5
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
                                 max_new_tokens=800, temperature=0.3)
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
