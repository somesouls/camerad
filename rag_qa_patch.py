# -*- coding: utf-8 -*-
"""rag_qa_patch.py — Fase 5: retrieval Q2Q (question-to-question) utk AWE & Sosmed.

Gagasan (usulan pengguna, 19 Agu 2026): data Sosmed & Livechat SUDAH berbentuk
pertanyaan-jawaban asli pengguna berbulan-bulan — bahasanya sangat mirip dengan
pertanyaan baru. Maka mesin mencari berdasarkan KEMIRIPAN PERTANYAAN (bukan
jawaban): pertanyaan historis di-embed (qa_index_db), jawabannya jadi jembatan,
dan rujukan peraturan yang terdeteksi di dalamnya (regref: "PMK 10 2025",
"PMK Nomor 10 Tahun 2025", "PMK No 10 TH 2025", dst.) ditautkan otomatis ke
basis peraturan yang rapi (jenis/nomor/tahun/pasal) — isi pasal ikut disertakan
sebagai konteks terverifikasi.

Cara pasang: membungkus _ctx_awe/_ctx_sosmed versi terakhir (v16,
rag_sources_patch) — jalur leksikal tetap jalan, hasil Q2Q DITAMBAHKAN.
Fail-soft penuh: bila qa.db belum dibangun / model tak tersedia, perilaku
kembali persis seperti v16.

Env:
  RAG_QA_PATCH=0      -> matikan patch ini.
  RAG_QA_MIN_COS=0.50 -> ambang cosine kemiripan pertanyaan.
  RAG_QA_TOPK=3       -> maks pasangan Q&A historis per query.
  RAG_QA_LINK_REG=0   -> jangan tautkan rujukan peraturan.

Diimpor di web_app.py SETELAH rag_sources_patch.
"""
import os

import rag_engine as _re

try:
    import qa_index_db as _qa
except Exception:            # pragma: no cover
    _qa = None
try:
    import peraturan_db as _pdb
except Exception:            # pragma: no cover
    _pdb = None

_orig_awe = getattr(_re, "_ctx_awe", None)
_orig_sosmed = getattr(_re, "_ctx_sosmed", None)


def _on():
    return str(os.environ.get("RAG_QA_PATCH", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _topk():
    try:
        return max(1, int(os.environ.get("RAG_QA_TOPK", "3")))
    except Exception:
        return 3


def _link_reg_on():
    return str(os.environ.get("RAG_QA_LINK_REG", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _reg_blocks(hits):
    """Dari hit Q&A, tautkan rujukan peraturan yang TERRESOLUSI: tarik isi pasal
    teratas (status berlaku) atau beri catatan bila rujukan kedaluwarsa.
    Kembalikan (blocks, sources); maks 2 blok peraturan total."""
    blocks, sources = [], []
    if _pdb is None or not _link_reg_on():
        return blocks, sources
    seen_lab = set()
    for h in hits[:2]:
        for reg in (h.get("regs") or [])[:3]:
            lab = str(reg.get("label") or "").strip()
            if not lab or lab in seen_lab:
                continue
            seen_lab.add(lab)
            if not reg.get("matched"):
                continue
            st = str(reg.get("status") or "").lower()
            if st in ("dicabut", "diubah"):
                blocks.append(
                    "Catatan status: rujukan historis %s tercatat berstatus %s "
                    "di basis data — gunakan pengganti/padanan terbarunya, "
                    "bukan kutipan lamanya." % (lab, st))
                continue
            kunci = "%s %s" % (reg.get("jenis") or "", reg.get("nomor") or "")
            if not kunci.strip():
                continue
            try:
                rows = _pdb.search(kunci, 2, ("berlaku",)) or []
            except Exception:
                rows = []
            for r in rows[:1]:
                try:
                    r = r if isinstance(r, dict) else dict(r)
                except Exception:
                    continue
                isi = str(r.get("isi") or "").strip()
                if not isi:
                    continue
                jenis = str(r.get("jenis_peraturan") or "").strip()
                nomor = str(r.get("nomor") or "").strip()
                tahun = str(r.get("tahun") or "").strip()
                pasal = str(r.get("pasal") or "").strip()
                head = " ".join(x for x in [jenis, nomor,
                                           ("Tahun " + tahun) if tahun else ""] if x).strip()
                if pasal:
                    head += " - Pasal " + pasal
                blocks.append(
                    "Peraturan tertaut otomatis dari jawaban historis (%s):\n%s\nIsi: %s"
                    % (lab, head, _re._clip(isi, 700)))
                sources.append({"sumber": "Peraturan",
                                "judul": head + " (tertaut dari jawaban historis)",
                                "ref": str(r.get("reference") or r.get("hierarchy") or ""),
                                "url": str(r.get("source_url") or "")})
            if len(blocks) >= 2:
                return blocks, sources
    return blocks, sources


def _qa_blocks(q, sumber):
    """(blocks, sources) tambahan dari indeks Q&A historis satu sumber."""
    blocks, sources = [], []
    if _qa is None:
        return blocks, sources
    try:
        hits = _qa.search(q, k=_topk())
    except Exception:
        hits = []
    hits = [h for h in hits if h.get("sumber") == sumber]
    if not hits:
        return blocks, sources
    for h in hits:
        qtext = _re._clip(h.get("question"), 300)
        atext = _re._clip(h.get("answer"), 450)
        if not (qtext and atext):
            continue
        blok = ("Pertanyaan serupa dari riwayat (kemiripan %.2f):\nTanya: %s\n"
                "Jawaban petugas saat itu: %s"
                % (float(h.get("cos") or 0.0), qtext, atext))
        blocks.append(blok)
        src = {"sumber": ("Percakapan AWE" if sumber == "awe" else "Data Sosmed"),
               "judul": ("Q&A serupa: " + _re._clip(qtext, 60)),
               "ref": str(h.get("ref_id") or ""),
               "url": str(h.get("url") or "")}
        sources.append(src)
    rb, rs = _reg_blocks(hits)
    blocks.extend(rb)
    sources.extend(rs)
    return blocks, sources


def _ctx_awe_qa(q, limit=3):
    text, sources = ("", [])
    if _orig_awe is not None:
        try:
            text, sources = _orig_awe(q, limit)
        except Exception:
            text, sources = "", []
    if not _on():
        return text, sources
    eb, es = _qa_blocks(q, "awe")
    if eb:
        text = (text + "\n\n" if text else "") + "\n\n".join(eb)
        sources = (sources or []) + es
    return text, sources


def _ctx_sosmed_qa(q, limit=3):
    text, sources = ("", [])
    if _orig_sosmed is not None:
        try:
            text, sources = _orig_sosmed(q, limit)
        except Exception:
            text, sources = "", []
    if not _on():
        return text, sources
    eb, es = _qa_blocks(q, "sosmed")
    if eb:
        text = (text + "\n\n" if text else "") + "\n\n".join(eb)
        sources = (sources or []) + es
    return text, sources


def _install():
    if not _on():
        print("[rag_qa_patch] dimatikan (RAG_QA_PATCH=0).", flush=True)
        return
    if getattr(_re, "_qa_patched", False):
        return
    try:
        _re._ctx_awe = _ctx_awe_qa
        _re._ctx_sosmed = _ctx_sosmed_qa
        if isinstance(getattr(_re, "_DISPATCH", None), dict):
            _re._DISPATCH["awe"] = _ctx_awe_qa
            _re._DISPATCH["sosmed"] = _ctx_sosmed_qa
    except Exception as e:
        print("[rag_qa_patch] gagal memasang: %s" % e, flush=True)
        return
    _re._qa_patched = True
    n = 0
    try:
        n = (_qa.stats() or {}).get("total_vec", 0) if _qa is not None else 0
    except Exception:
        n = 0
    print("[rag_qa_patch] Q2Q aktif (min_cos=%.2f, topk=%d, tautan_reg=%s; "
          "indeks Q&A: %s vektor). Bangun indeks: python phase5_qa_build.py"
          % (_qa.min_cos() if _qa is not None else 0.0, _topk(),
             _link_reg_on(), n if n else "BELUM ADA"), flush=True)


_install()
