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

v28: parameter "Maks. token / jawaban" (kartu Batas Penggunaan Harian di menu
Konfigurasi profil) kini BENAR-BENAR dibaca mesin — sebelumnya disimpan ke DB
tapi sintesis memakai max_new_tokens=800 hardcode. 0/kosong = default (800).

v30: sertakan konteks mentah hasil retrieval di kunci privat
"_konteks_internal" pada hasil (dibaca lalu LANGSUNG di-pop oleh
rag_grounding_patch sebelum respons keluar) agar guardrail anti-karang-pasal
menilai dukungan rujukan terhadap SELURUH konteks — bukan hanya judul/ref
sumber (akar abstain semu "gimana cara aktivasi EFIN?", 19 Agu 2026).

v31 (Tahap 4e): jawab_chat/jawab_lab menandai profil aktif ke rag.calibration
(set_profile) DI THREAD RETRIEVAL sehingga gerbang cosine (rag.calibration_patch)
+ knob retrieval dibaca PER-PROFIL dari rag.knob_store (precedence store-profil >
env > default). set_profile bersifat thread-local, jadi wajib dipanggil di thread
yang sama dengan pdb.search (jawab_chat berjalan via threadpool / thread latar,
bukan di handler async). INERT bila knob belum diset (jatuh ke env>default);
direset di finally agar tidak bocor antar-request pada worker threadpool.
"""
import re
import json
import os
import contextvars

import common.llm_client as llm_client
import common.pii_mask as pii_mask
import rag.router as rag_router
import rag.config_db as rcfg

try:
    import rag.rewrite as rag_rewrite
except Exception:            # pragma: no cover
    rag_rewrite = None
try:
    import rag.intent_semantic as ris
except Exception:            # pragma: no cover
    ris = None
try:
    import rag.reranker as rag_reranker
except Exception:            # pragma: no cover
    rag_reranker = None
try:
    import knowledge.intentmap_db as imdb
except Exception:            # pragma: no cover
    imdb = None
try:
    import avaya.db as avdb
except Exception:            # pragma: no cover
    avdb = None
try:
    import sosmed.db as sdb
except Exception:            # pragma: no cover
    sdb = None
try:
    import peraturan.db as pdb
except Exception:            # pragma: no cover
    pdb = None
try:
    import sop.db as sopdb
except Exception:            # pragma: no cover
    sopdb = None
try:
    import rag.calibration as _cal
except Exception:            # pragma: no cover
    _cal = None

MAKS_KONTEKS = 6500

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
        # (4) Untuk kueri PENDEK (sedikit token bermakna, mis. \"coretax\",
        # \"lupa efin\"), kueri biasanya merujuk LANGSUNG ke nama intent atau
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


def _sosmed_url(p):
    """Rangkai URL publik yang bisa diklik untuk item sosmed.

    Utamakan permalink; bila kosong, rakit dari platform + handle + external_id
    (mis. X/Twitter -> https://x.com/<handle>/status/<id>).
    """
    p = p or {}
    permalink = str(p.get("permalink") or "").strip()
    if permalink.startswith("http"):
        return permalink
    platform = str(p.get("platform") or "").strip().lower()
    handle = str(p.get("author_handle") or "").strip().lstrip("@")
    ext = str(p.get("external_id") or "").strip()
    if platform in ("x", "twitter") and ext:
        return "https://x.com/%s/status/%s" % (handle or "i", ext)
    return permalink


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
                        "ref": str(p.get("platform") or "").upper(),
                        "url": _sosmed_url(p)})
    return "\n\n".join(blocks), sources


def _ctx_peraturan(q, limit=4):
    if pdb is None:
        return "", []
    try:
        rows = pdb.search(q, limit, ("berlaku",))
    except Exception:
        rows = []
    clip_isi = _clip_peraturan_ctx.get()
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
        piece += "\nIsi: " + _clip(isi, clip_isi)
        blocks.append(piece)
        sources.append({"sumber": "Peraturan", "judul": head,
                        "ref": (reference or hierarchy),
                        "url": str(d.get("source_url") or "").strip()})
    return "\n\n".join(blocks), sources


def _ctx_sop(q, limit=4):
    if sopdb is None:
        return "", []
    try:
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


_DISPATCH = {
    "intent": _ctx_dialogflow,
    "awe": _ctx_awe,
    "sosmed": _ctx_sosmed,
    "peraturan": _ctx_peraturan,
    "sop": _ctx_sop,
}


def _retrieve_one(key, q):
    fn = _DISPATCH.get(key)
    if not fn:
        return "", []
    try:
        return fn(q)
    except Exception:
        return "", []


# ==========================================================================
# Sumber efektif, perakitan konteks, verifikasi, render prompt.
# ==========================================================================
def effective_sources(profile, override=None):
    """Tentukan sumber yang boleh dipakai mesin retrieval.

    - override (dari playground /rag-lab): daftar centang admin -> dipakai apa
      adanya.
    - produksi (chat): daftar 'sumber' (checkbox pada halaman \"RAG Agent -
      Konfigurasi\") BERSIFAT OTORITATIF. Sumber yang TIDAK dicentang tidak akan
      dipanggil maupun dikutip.

    Catatan perbaikan: dulu chip @sumber di dalam prompt (mis. \"@intent\")
    menimpa pilihan checkbox sehingga sumber yang sudah di-uncheck tetap
    terpakai. Sekarang chip pada prompt murni panduan naratif untuk LLM dan
    TIDAK lagi menentukan sumber retrieval.
    """
    valid = list(rcfg.SUMBER_VALID)
    if override is not None:
        return [s for s in override if s in valid]
    return [s for s in (profile.get("sumber") or []) if s in valid]


def _assemble(keys, cache, q):
    parts, sources, n = [], [], 0
    for key in keys:
        if key not in cache:
            cache[key] = _retrieve_one(key, q)
        t, s = cache[key]
        if t and t.strip():
            n += 1
            parts.append("### Sumber %d - %s\n%s" % (n, rcfg.SUMBER_LABEL.get(key, key), t))
            sources.extend(s)
    body = "\n\n".join(parts)
    if MAKS_KONTEKS and len(body) > MAKS_KONTEKS:
        body = body[:MAKS_KONTEKS].rstrip() + "\u2026"
    return body, sources


def _dedup_sources(sources):
    seen, out = set(), []
    for s in sources or []:
        key = (s.get("sumber", ""), s.get("judul", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _format_sumber(sources):
    lines = []
    for s in _dedup_sources(sources):
        line = "- [%s] %s" % (s.get("sumber", ""), s.get("judul", ""))
        ref = (s.get("ref") or "").strip()
        if ref:
            line += " (%s)" % ref
        lines.append(line)
    return "\n".join(lines)


def _verify(q, context):
    """Tahap 3: nilai kecukupan konteks. Fail-open (cukup=True) bila LLM error."""
    try:
        sys = (
            "Anda penilai konteks RAG. Diberi PERTANYAAN dan KONTEKS. Nilai "
            "apakah KONTEKS memuat cukup informasi untuk menjawab dengan benar. "
            "Jawab HANYA JSON valid: "
            '{\"cukup\":true/false,\"butuh_peraturan\":true/false,\"alasan\":\"singkat\"}. '
            "'butuh_peraturan' true bila jawaban memerlukan dasar hukum/pasal "
            "yang belum ada di konteks."
        )
        u = "PERTANYAAN:\n%s\n\nKONTEKS:\n%s" % (q, (context or "")[:4000])
        out = llm_client.chat([{"role": "user", "content": u}], system=sys,
                              max_new_tokens=120, temperature=0.0)
        m = re.search(r"\{.*\}", out or "", re.S)
        if m:
            d = json.loads(m.group(0))
            return {"cukup": bool(d.get("cukup")),
                    "butuh_peraturan": bool(d.get("butuh_peraturan")),
                    "alasan": str(d.get("alasan") or "")[:200]}
    except Exception as e:
        return {"cukup": True, "butuh_peraturan": False,
                "alasan": "verifikasi dilewati: " + str(e)[:120]}
    return {"cukup": True, "butuh_peraturan": False, "alasan": "format tak terbaca"}


def _render_prompt(tmpl, context, sumber_txt, fallback):
    t = tmpl or ""
    blok = "=== KONTEKS INTERNAL ===\n" + context + "\n=== AKHIR KONTEKS INTERNAL ==="
    if "{{konteks}}" in t:
        t = t.replace("{{konteks}}", blok)
    else:
        t = t + "\n\n" + blok
    if "{{sumber}}" in t:
        t = t.replace("{{sumber}}", sumber_txt or "(tidak ada sumber)")
    t = t.replace("{{fallback}}", fallback or "")
    return t


# ==========================================================================
# Profil 'cepat' & budget konteks per-profil.
# ==========================================================================
def _env_int(name, default=None):
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default


def _fast_profiles():
    """Kumpulan id profil yang dijalankan mode 'cepat'. Default: {'chatbot'}.
    Override via env RAG_FAST_PROFILES (dipisah koma; kosongkan untuk nonaktif)."""
    raw = os.environ.get("RAG_FAST_PROFILES")
    if raw is None:
        return {"chatbot"}
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _is_fast_profile(profile):
    pid = str((profile or {}).get("id") or "").strip().lower()
    return bool(pid) and pid in _fast_profiles()


def _clip_peraturan_for(profile):
    """Budget potong isi pasal peraturan untuk profil ini.
    Prioritas: env PERATURAN_CLIP_<ID> > env PERATURAN_CLIP > default
    (agent=1300 agar pasal panjang tak terpotong; lainnya=700)."""
    pid = str((profile or {}).get("id") or "").strip()
    n = _env_int("PERATURAN_CLIP_" + pid.upper())
    if n is None:
        n = _env_int("PERATURAN_CLIP")
    if n is None:
        n = 1300 if pid.lower() == "agent" else _CLIP_PERATURAN_DEFAULT
    return n if (isinstance(n, int) and n > 0) else _CLIP_PERATURAN_DEFAULT


def _fastpath_intent(q, profile, require_env=True):
    """Jalur cepat ala Dialogflow: jawab LANGSUNG dari intent katalog yang sudah
    diverifikasi analis & punya jawaban cuplikan, tanpa retrieval/LLM sintesis.

    Sengaja konservatif: hanya aktif bila env RAG_FASTPATH_INTENT=1 (kecuali
    require_env=False, mis. mode 'tanpa_llm' yang eksplisit meminta jalur ini),
    intent terverifikasi (terverifikasi=1), dan ada jawaban_cuplikan.
    Gagal-anggun -> None.
    """
    if require_env and str(os.environ.get("RAG_FASTPATH_INTENT", "0")).strip().lower() not in (
            "1", "true", "yes", "on"):
        return None
    if imdb is None:
        return None
    ql = (q or "").strip()
    if len(ql) < 3:
        return None
    try:
        c = imdb.init_db(imdb.connect())
    except Exception:
        return None
    try:
        try:
            imdb.init_catalog(c)
        except Exception:
            pass
        try:
            rows = imdb.match_catalog(c, ql, limit=1) or []
        except Exception:
            rows = []
    finally:
        try:
            c.close()
        except Exception:
            pass
    if not rows:
        return None
    d = rows[0]
    if int(d.get("terverifikasi") or 0) != 1:
        return None
    ans = str(d.get("jawaban_cuplikan") or "").strip()
    intent = str(d.get("intent") or "").strip()
    if not ans or not intent:
        return None
    return {"answer": ans,
            "sources": [{"sumber": "Training Phrase & Intent", "judul": intent, "ref": ""}]}


def _resolve_mode(profile):
    """Mode mesin per-profil (disetel via switch di halaman \"Webhook Chatbot\").
    Kembalikan '' (auto), 'tanpa_llm', 'llm', atau 'full'."""
    m = str((profile or {}).get("mode") or "").strip().lower()
    return m if m in ("tanpa_llm", "llm", "full") else ""


def _answer_no_llm(q, profile, allowed):
    """Mode 'tanpa LLM': jawab TANPA memanggil LLM generatif sama sekali.

    Urutan: (1) jalur cepat intent (jawaban cuplikan terverifikasi); (2) cuplikan
    teratas hasil retrieval dari sumber lain; (3) kalimat fallback. Router LLM &
    sintesis LLM sengaja dilewati. Catatan: retrieval tetap boleh memakai
    embedding/reranker (bukan LLM generatif) bila diaktifkan.
    """
    # (1) Jalur cepat intent - abaikan gerbang env karena mode ini eksplisit.
    if "intent" in allowed:
        fp = _fastpath_intent(q, profile, require_env=False)
        if fp:
            res = {"ok": True, "answer": fp["answer"], "grounded": True,
                   "profil": profile.get("id"), "domain": "intent", "jalur": "tanpa_llm"}
            if profile.get("tampil_sumber"):
                res["sources"] = _dedup_sources(fp["sources"])
            # v30: jawaban cuplikan terverifikasi analis merangkap bukti.
            res["_konteks_internal"] = fp["answer"]
            return res
    # (2) Cuplikan teratas dari sumber lain (tanpa sintesis LLM).
    order = [s for s in ("intent", "sop", "sosmed", "awe", "peraturan") if s in allowed]
    for key in order:
        t, s = _retrieve_one(key, q)
        if t and t.strip():
            res = {"ok": True, "answer": _clip(t, 1200), "grounded": True,
                   "profil": profile.get("id"),
                   "domain": ("peraturan" if key == "peraturan" else "umum"),
                   "jalur": "tanpa_llm"}
            if profile.get("tampil_sumber"):
                res["sources"] = _dedup_sources(s)
            # v30: cuplikan retrieval sebagai bukti dukungan guardrail.
            res["_konteks_internal"] = t
            return res
    # (3) Fallback.
    fallback = profile.get("fallback") or rcfg.FALLBACK_DEFAULT
    return {"ok": True, "answer": fallback, "grounded": False,
            "profil": profile.get("id"), "domain": "umum",
            "jalur": "tanpa_llm", "sources": []}


def _maks_token_for(profile):
    """v28: baca \"Maks. token / jawaban\" dari kartu Batas Penggunaan Harian
    (menu Konfigurasi profil; tersimpan di kuota agent_log_db per target =
    id profil). 0/kosong/error -> default mesin 800. Gagal-anggun penuh."""
    try:
        import db.agent_log_db as _aldb
        q = _aldb.get_quota(str((profile or {}).get("id") or ""))
        mt = int((q or {}).get("maks_token") or 0)
        return mt if mt > 0 else 800
    except Exception:
        return 800


# ==========================================================================
# Orkestrasi pipeline.
# ==========================================================================
def answer(question, profile, override=None, history=None, diagnostics=False,
           honor_mode=False):
    q = (question or "").strip()
    fallback = profile.get("fallback") or rcfg.FALLBACK_DEFAULT
    if not q:
        return {"ok": False, "error": "Pertanyaan kosong."}

    # Mode mesin per-profil (switch di halaman \"Webhook Chatbot\"):
    #   ''(auto)    -> ikut env (profil 'cepat' seperti perilaku bawaan)
    #   'tanpa_llm' -> TANPA LLM generatif (jalur cepat intent + cuplikan retrieval)
    #   'llm'       -> pakai LLM tapi hemat (tanpa loop verifikasi & AI-rewrite)
    #   'full'      -> pipeline penuh (AI-rewrite + loop verifikasi + sintesis)
    # Playground /rag-lab (diagnostics=True) biasanya memaksa pipeline penuh.
    # Namun bila honor_mode=True (opsi \"mode produksi\" di RAG Lab), tetap ikuti
    # mode NYATA profil (mis. 'cepat' untuk chatbot) supaya diagnostik
    # mencerminkan perilaku produksi sebenarnya.
    mode = _resolve_mode(profile)
    if diagnostics and not honor_mode:
        no_llm, fast = False, False
    elif mode == "full":
        no_llm, fast = False, False
    elif mode == "llm":
        no_llm, fast = False, True
    elif mode == "tanpa_llm":
        no_llm, fast = True, False
    else:
        no_llm, fast = False, _is_fast_profile(profile)

    # Budget potong isi pasal peraturan per-profil (agent lebih longgar) ->
    # dibaca _ctx_peraturan lewat contextvar.
    try:
        _clip_peraturan_ctx.set(_clip_peraturan_for(profile))
    except Exception:
        pass
    # Tandai profil aktif ke modul rewrite; AI-rewrite (LLM) dilewati untuk mode
    # 'cepat' maupun 'tanpa_llm'. Aman bila modul tak tersedia.
    if rag_rewrite is not None:
        try:
            rag_rewrite.set_context(profile.get("id"), fast=(fast or no_llm))
        except Exception:
            pass

    # [1] Sumber efektif (checkbox profil / override playground).
    allowed = effective_sources(profile, override)

    # Mode TANPA LLM: jawab tanpa memanggil LLM generatif sama sekali.
    if no_llm:
        return _answer_no_llm(q, profile, allowed)

    diag = {"router": None, "retrieval": [], "verifikasi": [],
            "sumber_dipakai": [], "eskalasi": []}

    # [1b] Jalur cepat FAQ/intent (ala Dialogflow): jawab langsung dari intent
    # terverifikasi tanpa retrieval/LLM. Opt-in via RAG_FASTPATH_INTENT=1.
    if fast and "intent" in allowed:
        fp = _fastpath_intent(q, profile)
        if fp:
            res = {"ok": True, "answer": fp["answer"], "grounded": True,
                   "profil": profile.get("id"), "domain": "intent", "jalur": "cepat"}
            if profile.get("tampil_sumber"):
                res["sources"] = _dedup_sources(fp["sources"])
            # v30: jawaban cuplikan terverifikasi analis merangkap bukti.
            res["_konteks_internal"] = fp["answer"]
            return res

    r = rag_router.route(q, allowed)
    diag["router"] = r
    # Jaga-jaga: kunci urutan router agar tidak pernah keluar dari daftar sumber
    # yang diizinkan (checkbox). Ini menjamin sumber yang di-uncheck benar-benar
    # tidak dipakai, apa pun keluaran router.
    ordered = [s for s in r["ordered"] if s in allowed]

    # Tunda Peraturan bila domain bukan hukum -> biar loop verifikasi yang
    # memicu pencarian peraturan (persis skenario yang diminta). Untuk profil
    # cepat, maks_loop dipaksa 0 (tanpa loop verifikasi LLM).
    maks_loop = 0 if fast else int(profile.get("maks_loop") or 0)
    defer = set()
    if "peraturan" in ordered and r["domain"] in ("aplikasi", "umum") and maks_loop > 0:
        defer.add("peraturan")
    active = [s for s in ordered if s not in defer]

    # [2] Retrieval awal
    cache = {}
    context, sources = _assemble(active, cache, q)

    # [3] Verifikasi + eskalasi (loop maksimal maks_loop)
    loops = 0
    while loops < maks_loop:
        if context.strip():
            v = _verify(q, context)
        else:
            v = {"cukup": False, "butuh_peraturan": True, "alasan": "konteks kosong"}
        diag["verifikasi"].append(dict(putaran=loops + 1, **v))
        if v.get("cukup"):
            break
        added = False
        if v.get("butuh_peraturan") and "peraturan" in defer:
            defer.discard("peraturan")
            active = [s for s in ordered if s not in defer]
            context, sources = _assemble(active, cache, q)
            diag["eskalasi"].append("putaran %d: tambah sumber Peraturan" % (loops + 1))
            added = True
        if not added:
            break
        loops += 1

    # Diagnostik retrieval
    for key in active:
        t, s = cache.get(key, ("", []))
        diag["retrieval"].append({
            "sumber": key, "label": rcfg.SUMBER_LABEL.get(key, key),
            "jumlah": len(s), "dipakai": bool((t or "").strip()),
            "hits": [{"judul": x.get("judul", ""), "ref": x.get("ref", "")} for x in s[:5]],
        })
    diag["sumber_dipakai"] = [k for k in active if (cache.get(k, ("", []))[0] or "").strip()]

    # [5] Post-check: tanpa konteks -> fallback
    if not context.strip():
        res = {"ok": True, "answer": fallback, "grounded": False,
               "profil": profile.get("id"), "domain": r["domain"], "sources": []}
        if diagnostics:
            res["diagnostics"] = diag
        return res

    # [4] Sintesis grounded
    sumber_txt = _format_sumber(sources)
    system = _render_prompt(profile.get("system_prompt"), context, sumber_txt, fallback)
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
        ans = llm_client.chat(msgs, system=pii_mask.mask_text(system),
                              max_new_tokens=_maks_token_for(profile),
                              temperature=float(profile.get("suhu") or 0.3))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not (ans or "").strip():
        ans = fallback

    res = {"ok": True, "answer": ans, "grounded": True,
           "profil": profile.get("id"), "domain": r["domain"]}
    # v30: teruskan konteks mentah ke rag_grounding_patch lewat kunci privat
    # (patch SELALU mem-pop kunci ini sebelum respons keluar — tak bocor ke
    # klien) agar bukti dukungan rujukan = SELURUH konteks, bukan judul/ref saja.
    res["_konteks_internal"] = context
    if profile.get("tampil_sumber") or diagnostics:
        res["sources"] = _dedup_sources(sources)
    if diagnostics:
        res["diagnostics"] = diag
        res["prompt_final"] = system[:4000]
    return res


def _set_profile_cal(pid):
    """Tandai profil aktif (thread-local rag.calibration) agar gerbang cosine
    (rag.calibration_patch) + resolusi knob memakai nilai PER-PROFIL dari
    rag.knob_store. WAJIB dipanggil di thread retrieval (jawab_chat/jawab_lab
    berjalan via threadpool / thread latar), bukan di handler async, karena
    set_profile bersifat thread-local. INERT bila knob belum diset (jatuh ke
    env>default). Gagal-anggun."""
    if _cal is None:
        return
    try:
        _cal.set_profile((pid or "").strip() or None)
    except Exception:
        pass


def _reset_profile_cal():
    """Bersihkan profil aktif thread-local (finally) agar tak bocor ke tugas
    berikutnya pada worker threadpool yang dipakai ulang. Gagal-anggun."""
    if _cal is None:
        return
    try:
        _cal.reset_profile()
    except Exception:
        pass


def jawab_chat(question, history=None, profil="chatbot"):
    p = rcfg.get_profile(profil) or rcfg.get_profile("chatbot")
    if not p:
        return {"ok": False, "error": "Profil RAG belum tersedia."}
    _set_profile_cal(p.get("id") or profil)
    try:
        return answer(question, p, override=None, history=history, diagnostics=False)
    finally:
        _reset_profile_cal()


def jawab_lab(question, profil, sumber_override, history=None, prod_mode=False):
    p = rcfg.get_profile(profil) or rcfg.get_profile("chatbot")
    if not p:
        return {"ok": False, "error": "Profil RAG belum tersedia."}
    if sumber_override is not None and not isinstance(sumber_override, list):
        sumber_override = None
    _set_profile_cal(p.get("id") or profil)
    try:
        return answer(question, p, override=sumber_override, history=history,
                      diagnostics=True, honor_mode=bool(prod_mode))
    finally:
        _reset_profile_cal()
