# -*- coding: utf-8 -*-
"""rag_citation_filter_patch.py — PR A: sitasi inline + filter sumber dipakai.

Masalah yang diperbaiki
------------------------
``rag_engine.answer`` mengembalikan ``sources = _dedup_sources(sources)`` yaitu
SELURUH hit retrieval yang masuk ke konteks LLM — bukan hanya sumber yang
BENAR-BENAR dirujuk pada jawaban. Akibatnya UI (halaman "/") menampilkan semua
tautan yang masuk mesin RAG, termasuk yang tidak dipakai untuk menjawab. Ini
menyulitkan agent memverifikasi jawaban.

Solusi (murni monkey-patch; TIDAK mengubah engine.py / config_db.py)
-------------------------------------------------------------------
1. ``_format_sumber`` dibungkus agar daftar ``{{sumber}}`` yang dilihat LLM
   DINOMORI ``[1] [Sumber] Judul (ref)``. Nomor mengikuti urutan
   ``_dedup_sources`` — IDENTIK dengan urutan ``res["sources"]`` — sehingga
   penanda ``[n]`` pada jawaban bisa dipetakan balik ke elemen sumber.
2. ``_render_prompt`` dibungkus agar aturan sitasi ditambahkan ke system prompt
   (tandai tiap klaim dengan ``[n]`` sesuai nomor sumber).
3. ``answer`` dibungkus (lapis TERLUAR) agar setelah sintesis:
   - kumpulkan indeks ``[n]`` yang muncul di ``answer``;
   - saring ``res["sources"]`` -> hanya sumber yang nomornya dikutip;
   - tandai ``s["cited"]=True`` dan ``s["cite_no"]=n`` untuk dipakai frontend;
   - simpan ``res["sources_all_count"]`` = jumlah sumber sebelum disaring.
   Gagal-anggun: bila TIDAK ada penanda ``[n]`` sama sekali (mis. model
   mengabaikan instruksi), sumber DIBIARKAN apa adanya agar tak menyembunyikan
   semua rujukan.

Knob: env ``RAG_CITATION_FILTER`` (default 1). Set 0 untuk menonaktifkan.
Fail-open penuh: error apa pun -> perilaku lama.

WAJIB diimpor di web_app.py SETELAH rag_grounding_patch & handoff_routing_patch
agar membungkus versi ``answer`` terakhir (lapis terluar).
"""
import os
import re

import rag.engine as _eng

_orig_format_sumber = getattr(_eng, "_format_sumber", None)
_orig_render_prompt = getattr(_eng, "_render_prompt", None)
_orig_answer = getattr(_eng, "answer", None)

_MARK_RE = re.compile(r"\[(\d{1,3})\]")


def _enabled():
    v = str(os.environ.get("RAG_CITATION_FILTER", "1")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _numbered_sumber(sources):
    """Versi bernomor daftar sumber untuk placeholder ``{{sumber}}``.

    Urutan mengikuti ``_dedup_sources(sources)`` -> identik dgn ``res[sources]``.
    """
    try:
        deduped = _eng._dedup_sources(sources)
    except Exception:
        deduped = sources or []
    lines = []
    for i, s in enumerate(deduped, 1):
        line = "[%d] [%s] %s" % (i, s.get("sumber", ""), s.get("judul", ""))
        ref = (s.get("ref") or "").strip()
        if ref:
            line += " (%s)" % ref
        lines.append(line)
    return "\n".join(lines)


def _format_sumber_patched(sources):
    if not _enabled() or _orig_format_sumber is None:
        return _orig_format_sumber(sources) if _orig_format_sumber else ""
    try:
        return _numbered_sumber(sources)
    except Exception:
        return _orig_format_sumber(sources)


_CITE_GUIDE = (
    "\n\nATURAN SITASI (WAJIB):\n"
    "- Setiap pernyataan faktual yang diambil dari KONTEKS INTERNAL harus "
    "diberi penanda rujukan berupa nomor sumber dalam kurung siku, mis. [1] "
    "atau [2], DILETAKKAN DI AKHIR kalimat/klausa yang didukungnya.\n"
    "- Nomor mengacu pada daftar sumber (\"[1] ...\", \"[2] ...\").\n"
    "- Boleh menggabungkan beberapa sumber: [1][3].\n"
    "- JANGAN mengarang nomor yang tidak ada di daftar. Hanya kutip sumber "
    "yang benar-benar Anda pakai untuk menyusun jawaban.\n"
    "- Jangan menampilkan ulang daftar sumber di akhir jawaban; cukup penanda "
    "[n] pada kalimatnya."
)


def _render_prompt_patched(tmpl, context, sumber_txt, fallback):
    base = _orig_render_prompt(tmpl, context, sumber_txt, fallback)
    if not _enabled():
        return base
    try:
        return base + _CITE_GUIDE
    except Exception:
        return base


def _filter_cited(res):
    """Saring ``res[sources]`` ke sumber yang nomornya dikutip di ``res[answer]``."""
    try:
        ans = res.get("answer") or ""
        srcs = res.get("sources") or []
        if not srcs or not ans:
            return res
        nums = set()
        for m in _MARK_RE.finditer(ans):
            try:
                nums.add(int(m.group(1)))
            except Exception:
                pass
        cited = [n for n in nums if 1 <= n <= len(srcs)]
        if not cited:
            # Tidak ada penanda valid -> jangan sembunyikan apa pun (gagal-anggun).
            return res
        kept = []
        for n in sorted(cited):
            s = dict(srcs[n - 1])
            s["cited"] = True
            s["cite_no"] = n
            kept.append(s)
        res["sources_all_count"] = len(srcs)
        res["sources"] = kept
        return res
    except Exception:
        return res


def _answer_patched(*args, **kwargs):
    res = _orig_answer(*args, **kwargs)
    if not _enabled():
        return res
    try:
        if isinstance(res, dict) and res.get("ok") and res.get("grounded"):
            res = _filter_cited(res)
    except Exception:
        pass
    return res


if _orig_format_sumber is not None:
    _eng._format_sumber = _format_sumber_patched
if _orig_render_prompt is not None:
    _eng._render_prompt = _render_prompt_patched
if _orig_answer is not None:
    _eng.answer = _answer_patched
