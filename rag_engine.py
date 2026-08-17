# -*- coding: utf-8 -*-
"""rag_engine.py — Inti mesin RAG (pipeline 5 tahap + loop verifikasi).

Tahap:
  [0] Guardrail + PII masking (pii_mask)
  [1] Router (rag_router) -> urutan prioritas sumber sesuai domain
  [2] Retrieval per sumber (FTS5 + semantik e5 bila tersedia)
  [3] Verifikasi kecukupan (LLM) -> eskalasi ambil Peraturan bila perlu (loop)
  [4] Sintesis jawaban grounded (llm_client.chat, persona dari profil)
  [5] Post-check -> fallback bila tak ada konteks

Profil (persona/prompt/sumber) diambil dari rag_config_db. Dipakai oleh
rag_routes.py untuk endpoint chat (produksi) dan playground admin (/rag-lab).
"""
import re
import json
import os
import contextvars

import llm_client
import pii_mask
import rag_router
import rag_config_db as rcfg

try:
    import rag_rewrite
except Exception:            # pragma: no cover
    rag_rewrite = None
try:
    import rag_intent_semantic as ris
except Exception:            # pragma: no cover
    ris = None
try:
    import rag_reranker
except Exception:            # pragma: no cover
    rag_reranker = None
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

MAKS_KONTEKS = 6500

# --- Perbaikan perakitan konteks (audit empiris) --------------------------
# Anggaran karakter per-sumber saat merakit konteks. Menggantikan pemotongan
# EKOR global yang dulu membuang sumber berprioritas tinggi (mis. Intent) hanya
# karena berada di belakang string gabungan. Sekarang tiap sumber diisi
# mengikuti urutan prioritas router, dibatasi anggaran ini, hingga total
# mendekati MAKS_KONTEKS. Override: env RAG_SUMBER_BUDGET (per sumber),
# RAG_MAKS_KONTEKS (total).
_SUMBER_BUDGET_DEFAULT = 3500

# Status peraturan yang boleh masuk konteks. Dulu hanya ("berlaku",) sehingga
# pasal berstatus "diubah" (mis. PP 55/2022, UU 28/2007) tak pernah terjangkau
# padahal masih menjadi rujukan relevan. Sertakan "diubah". Override: env
# PERATURAN_STATUS (dipisah koma).
_PERATURAN_STATUS_DEFAULT = ("berlaku", "diubah")


def _peraturan_status():
    raw = os.environ.get("PERATURAN_STATUS")
    if raw is None or not str(raw).strip():
        return _PERATURAN_STATUS_DEFAULT
    vals = tuple(x.strip().lower() for x in str(raw).split(",") if x.strip())
    return vals or _PERATURAN_STATUS_DEFAULT


# Budget potong isi pasal peraturan (per blok). Agent perlu lebih panjang agar
# pasal tidak terpotong; chatbot cukup ringkas. Diset per-request oleh answer()
# lewat contextvar (aman untuk banyak request paralel).
_CLIP_PERATURAN_DEFAULT = 700
_clip_peraturan_ctx = contextvars.ContextVar(
    "rag_clip_peraturan", default=_CLIP_PERATURAN_DEFAULT)

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


def _intent_topk():
    """Berapa banyak intent teratas (hasil fusi leksikal+semantik) diambil ke
    konteks. Dinaikkan dari 4 -> default 6 untuk memperbaiki recall. Override
    via env RAG_INTENT_TOPK."""
    try:
        return max(1, int(os.environ.get("RAG_INTENT_TOPK", "6")))
    except Exception:
        return 6


def _rerank_intent_picks(q, picks, top_k):
    """Rerank cross-encoder atas kandidat intent hasil fusi (opsional).

    picks: list entri {"d": <katalog dict>, "skor": float} terurut menurun.
    Mengembalikan list entri yang SAMA namun terurut ulang oleh cross-encoder
    (rag_reranker). Hanya pool teratas yang dinilai ulang demi hemat latensi;
    sisanya ditempel di belakang. Bila reranker nonaktif/tak tersedia atau
    error, kembalikan picks apa adanya (gagal-anggun).
    """
    if rag_reranker is None or not picks or len(picks) < 2:
        return picks
    try:
        if not rag_reranker.is_available():
            return picks
    except Exception:
        return picks
    try:
        pool_n = max(int(top_k) * 3, 8)
    except Exception:
        pool_n = 12
    pool, rest = picks[:pool_n], picks[pool_n:]
    rows = []
    for e in pool:
        d = (e or {}).get("d") or {}
        judul = str(d.get("intent") or "")
        isi = (str(d.get("jawaban_cuplikan") or "").strip()
               or str(d.get("deskripsi_cakupan") or "").strip()
               or str(d.get("deskripsi_maksud") or "").strip())
        rows.append({"judul": judul, "isi": isi, "_e": e})
    try:
        ordered = rag_reranker.rerank(q, rows, top_k=None) or []
    except Exception:
        return picks
    out = [r["_e"] for r in ordered if isinstance(r, dict) and r.get("_e") is not None]
    return (out + rest) if out else picks


# ==========================================================================
# Tahap 2 — Retrieval per sumber (identik dengan pilot; dipindah ke engine).
# ==========================================================================
def _ctx_dialogflow(q):
    """Retrieval intent HYBRID: gabungan pencocokan LEKSIKAL (hitung token) dan
    SEMANTIK (embedding via rag_intent_semantic). Leksikal kuat untuk kata kunci
    persis; semantik menutup parafrase/sinonim/salah ketik (mis. \"mengubah
    pekerjaan\" -> intent \"...Perubahan Data\"; \"error PPN\" -> \"Kode Error...\").
    Kedua peringkat digabung dengan Reciprocal Rank Fusion lalu diambil top-K.
    Gagal-anggun: bila modul/model semantik tak tersedia, otomatis leksikal saja.
    """
    if imdb is None:
        return "", []
    try:
        c = imdb.init_db(imdb.connect())
    except Exception:
        return "", []
    blocks, sources = [], []
    toks = _tokens(q, k=12)
    # Perluas token pencocokan LEKSIKAL dengan kamus sinonim/akronim (non-LLM,
    # jadi tetap jalan pada mode cepat). Mis. "error PPN" -> tambah token
    # "pajak","pertambahan","nilai" agar intent "Kode Error ... PPN" ikut terjaring.
    try:
        if rag_rewrite is not None:
            for _term in (rag_rewrite.expand_kamus(q) or []):
                for _t in _tokens(_term, k=6):
                    if _t not in toks:
                        toks.append(_t)
    except Exception:
        pass
    try:
        try:
            imdb.init_catalog(c)
        except Exception:
            pass
        cat_rows = []
        try:
            cat_rows = c.execute(
                "SELECT intent, deskripsi_maksud, deskripsi_cakupan, "
                "jawaban_cuplikan, training_phrase_contoh FROM intentmap_catalog "
                "WHERE COALESCE(sumber_status,'aktif')!='hilang' "
                "AND COALESCE(soft_deleted,0)=0"
            ).fetchall()
        except Exception:
            cat_rows = []

        # (A) Peringkat LEKSIKAL — hitung kemunculan token (kuat utk kata kunci).
        # (4) Untuk kueri PENDEK (sedikit token bermakna, mis. "coretax",
        # "lupa efin"), kueri biasanya merujuk LANGSUNG ke nama intent atau
        # contoh training phrase, bukan uraian panjang. Maka beri bobot lebih
        # besar pada kecocokan NAMA-INTENT & TRAINING PHRASE untuk kueri pendek.
        short_n = _env_int("RAG_INTENT_SHORTQ_TOKENS", 3) or 3
        name_boost = _env_int("RAG_INTENT_NAME_BOOST", 3) or 3
        tp_boost = _env_int("RAG_INTENT_TP_BOOST", 2) or 2
        is_short_q = 0 < len(toks) <= short_n
        lex_scored = []
        if toks:
            for r in cat_rows:
                d = dict(r)
                tps = _json_list(d.get("training_phrase_contoh"))
                iname = str(d.get("intent") or "")
                iname_l = iname.lower()
                tp_hay = " ".join(str(x) for x in tps).lower()
                hay = " ".join([
                    iname, str(d.get("deskripsi_maksud") or ""),
                    str(d.get("deskripsi_cakupan") or ""),
                    str(d.get("jawaban_cuplikan") or ""),
                    tp_hay,
                ]).lower()
                score = sum(hay.count(t) for t in toks)
                name_hits = sum(1 for t in toks if t in iname_l)
                score += 2 * name_hits
                if is_short_q:
                    score += name_boost * name_hits
                    score += tp_boost * sum(tp_hay.count(t) for t in toks)
                if score > 0:
                    lex_scored.append((score, d))
            lex_scored.sort(key=lambda x: -x[0])
        lex_order = [d for _, d in lex_scored]

        # (B) Peringkat SEMANTIK — embedding katalog (parafrase/sinonim/typo).
        sem_ranked = []
        if ris is not None:
            try:
                sem_ranked = ris.rank(q, limit=max(6, _intent_topk() * 2)) or []
            except Exception:
                sem_ranked = []

        # (C) Fusi peringkat (Reciprocal Rank Fusion) leksikal + semantik, plus
        #     dorongan kecil dari cosine agar kecocokan semantik kuat naik.
        fused = {}
        _RRF_K = 60.0
        for i, d in enumerate(lex_order):
            nm = str(d.get("intent") or "")
            if not nm:
                continue
            e = fused.setdefault(nm, {"d": d, "skor": 0.0})
            e["skor"] += 1.0 / (_RRF_K + i + 1)
        for i, item in enumerate(sem_ranked):
            try:
                d, s = item
            except Exception:
                d, s = item, 0.0
            nm = str((d or {}).get("intent") or "")
            if not nm:
                continue
            e = fused.setdefault(nm, {"d": d, "skor": 0.0})
            e["skor"] += 1.0 / (_RRF_K + i + 1)
            e["skor"] += 0.15 * float(s or 0)

        picks = sorted(fused.values(), key=lambda e: -e["skor"])
        # (C2) Rerank cross-encoder (opsional, env RAG_RERANK): baca pasangan
        # (pertanyaan, kandidat) sekaligus untuk skor relevansi lebih akurat
        # atas hasil fusi. Gagal-anggun -> urutan fusi dipertahankan.
        top_k = _intent_topk()
        picks = _rerank_intent_picks(q, picks, top_k)
        for e in picks[:top_k]:
            d = e["d"]
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

        # (D) Kebijakan pemetaan intent analis (imdb.match) — tetap disuntik.
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
                for ent in m:
                    nm = ent.get("intent") if isinstance(ent, dict) else None
                    if nm and not any(s["judul"] == nm for s in sources):
                        sources.append({"sumber": "Training Phrase & Intent", "judul": nm, "ref": ""})
    finally:
        try:
            c.close()
        except Exception:
            pass
    return "\n\n".join(blocks), sources


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
