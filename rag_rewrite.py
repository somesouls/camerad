# -*- coding: utf-8 -*-
"""rag_rewrite.py — Query rewriting + kamus sinonim untuk retrieval RAG (Tahap 5).

Menjembatani "vocabulary mismatch": pertanyaan awam pengguna sering jauh dari
bahasa hukum/formal pada korpus peraturan. Modul ini memperkaya query SEBELUM
retrieval hybrid (dipakai lewat rag_rerank_patch yang membungkus
peraturan_db.search):

  1. expand_kamus(q)  -> tambah istilah baku/sinonim dari rag_kamus_db.
  2. rewrite_ai(q)    -> FITUR REWRITING OTOMATIS AI: mengubah pertanyaan awam
                         menjadi query hukum formal + menebak jenis peraturan /
                         pasal / istilah terkait (hanya sebagai PETUNJUK
                         pencarian, BUKAN fakta jawaban).
  3. untuk_retrieval(q) -> gabungan query efektif (q + kamus + istilah formal AI)
                          yang dipakai untuk retrieval. Rerank tetap memakai
                          query ASLI.

AI rewriting hanya dijalankan untuk pertanyaan bergaya natural (bukan sekadar
kutipan nomor peraturan) dan hasilnya di-cache per-query agar tidak memanggil
LLM berkali-kali dalam satu jawaban.

Konfigurasi (env):
  RAG_REWRITE_AI     '1' (default) aktif; '0' matikan (kamus tetap jalan).
Gagal-anggun: setiap kegagalan -> kembali ke query asli/tanpa AI.
"""
import os
import re
import json

try:
    import llm_client
except Exception:            # pragma: no cover
    llm_client = None
try:
    import rag_kamus_db as kdb
except Exception:            # pragma: no cover
    kdb = None

_CACHE = {}
_CACHE_MAKS = 256

# Pola kutipan peraturan (mis. "PMK 66/2024", "PER-11/PJ/2020", "UU 7 tahun 2021").
_RE_SITASI = re.compile(
    r"\b(uu|pp|perpu|perpres|pmk|per|se|kmk|kep|pj)\b[\s\-/]*\d",
    re.IGNORECASE,
)
_KATA_TANYA = ("apa", "apakah", "bagaimana", "gimana", "kenapa", "mengapa",
               "berapa", "kapan", "siapa", "mana", "dimana", "bisakah",
               "bolehkah", "kena", "termasuk", "wajib", "cara")


def _ai_enabled():
    return str(os.environ.get("RAG_REWRITE_AI", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def expand_kamus(q):
    """Daftar istilah tambahan dari kamus sinonim (list[str])."""
    if kdb is None:
        return []
    try:
        return kdb.expand_terms(q)
    except Exception:
        return []


def _is_natural(q):
    """True bila query layak di-rewrite AI (pertanyaan natural, bukan kutipan)."""
    ql = (q or "").strip().lower()
    if len(ql) < 8:
        return False
    if _RE_SITASI.search(ql):
        return False
    n_kata = len(re.findall(r"\w+", ql))
    if n_kata < 3:
        return False
    if any(k in ql.split() for k in _KATA_TANYA):
        return True
    return n_kata >= 5


_SYS_REWRITE = (
    "Anda asisten pencarian hukum perpajakan Indonesia. Ubah pertanyaan awam "
    "pengguna menjadi kueri pencarian bergaya bahasa peraturan yang formal, dan "
    "tebak konteks peraturannya. Jawab HANYA JSON valid tanpa penjelasan lain, "
    "berbentuk: {\"query_formal\":\"...\",\"istilah\":[\"...\"],"
    "\"jenis_peraturan\":[\"...\"],\"pasal_dugaan\":[\"...\"]}. "
    "PENTING: 'pasal_dugaan' & 'jenis_peraturan' hanyalah DUGAAN untuk membantu "
    "pencarian, bukan kepastian hukum. Jangan mengarang nomor peraturan spesifik "
    "bila tidak yakin; kosongkan saja. Gunakan istilah baku pajak (mis. 'objek "
    "Pajak Pertambahan Nilai', 'Pengusaha Kena Pajak')."
)


def rewrite_ai(q, force=False):
    """FITUR AI: rewrite pertanyaan -> dict petunjuk pencarian (di-cache).

    Kembalikan dict: {query_formal, istilah[], jenis_peraturan[], pasal_dugaan[],
    dipakai(bool), alasan}. Gagal-anggun: dipakai=False + query asli.
    """
    q = (q or "").strip()
    base = {"query_formal": q, "istilah": [], "jenis_peraturan": [],
            "pasal_dugaan": [], "dipakai": False, "alasan": ""}
    if not q:
        return base
    if q in _CACHE:
        return _CACHE[q]
    if not force:
        if not _ai_enabled():
            base["alasan"] = "AI rewriting dimatikan (RAG_REWRITE_AI=0)."
            return base
        if not _is_natural(q):
            base["alasan"] = "Query bukan pertanyaan natural; AI dilewati."
            return base
    if llm_client is None:
        base["alasan"] = "llm_client tak tersedia."
        return base
    try:
        out = llm_client.chat([{"role": "user", "content": q}],
                              system=_SYS_REWRITE, max_new_tokens=220, temperature=0.1)
        m = re.search(r"\{.*\}", out or "", re.S)
        if not m:
            base["alasan"] = "format keluaran tak terbaca."
            return base
        d = json.loads(m.group(0))
        res = {
            "query_formal": str(d.get("query_formal") or q).strip() or q,
            "istilah": [str(x).strip() for x in (d.get("istilah") or []) if str(x).strip()][:8],
            "jenis_peraturan": [str(x).strip() for x in (d.get("jenis_peraturan") or []) if str(x).strip()][:6],
            "pasal_dugaan": [str(x).strip() for x in (d.get("pasal_dugaan") or []) if str(x).strip()][:6],
            "dipakai": True,
            "alasan": "",
        }
        if len(_CACHE) < _CACHE_MAKS:
            _CACHE[q] = res
        return res
    except Exception as e:
        base["alasan"] = "gagal: " + str(e)[:120]
        return base


def untuk_retrieval(q):
    """Query efektif untuk retrieval = q + kamus + istilah formal AI.

    Dipakai rag_rerank_patch sebelum memanggil peraturan_db.search asli.
    Rerank & gerbang cosine tetap memakai query ASLI (bukan hasil ini).
    """
    q = (q or "").strip()
    if not q:
        return q
    frags = [q]

    def _add(frag):
        f = str(frag or "").strip()
        if f and f.lower() not in [x.lower() for x in frags]:
            frags.append(f)

    for t in expand_kamus(q):
        _add(t)
    try:
        ai = rewrite_ai(q)
        if ai.get("dipakai"):
            _add(ai.get("query_formal"))
            for t in ai.get("istilah") or []:
                _add(t)
    except Exception:
        pass
    return " ".join(frags)[:600]
