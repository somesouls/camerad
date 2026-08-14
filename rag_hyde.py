# -*- coding: utf-8 -*-
"""
rag_hyde.py — HyDE (Hypothetical Document Embeddings) untuk retrieval peraturan.

Ide HyDE
--------
Jurang terbesar RAG hukum di sini adalah GAP BAHASA: pertanyaan user informal/
slang/typo ("npwp ku ilang gmn ngurusnya"), sedangkan pasal ditulis sangat
formal ("Dalam hal Wajib Pajak kehilangan kartu NPWP, Wajib Pajak dapat
mengajukan permohonan cetak ulang..."). Embedding e5 dari pertanyaan mentah
sering berjarak jauh dari embedding pasal.

HyDE menjembatani: LLM diminta MENGARANG jawaban HIPOTETIS singkat bergaya
peraturan (formal, diksi hukum) lebih dulu, lalu embedding DARI teks hipotetis
itulah yang dipakai untuk pencarian dense. Dokumen hipotetis tak harus faktual
benar — ia hanya 'umpan' agar vektor mendarat di tetangga yang tepat. Fakta
final tetap berasal dari pasal asli hasil retrieval, bukan dari dokumen HyDE.

Sifat
-----
- OPSIONAL & default NONAKTIF (env RAG_HYDE). Menambah 1 panggilan LLM per
  query, jadi buruk untuk chatbot cepat; cocok untuk profil 'agent'.
  Pengaktifan per-profil diatur di rag_hyde_patch.py (default hanya 'agent').
- FAIL-OPEN: bila LLM gagal/timeout/kosong -> kembalikan query asli.
- Ada cache (LRU sederhana) agar pertanyaan berulang tak memanggil LLM lagi.

Catatan desain: kita menggabungkan 'query asli + dokumen hipotetis' (bukan
dokumen hipotetis saja) agar jangkar kata kunci asli (NPWP, SPT, PPh) tetap
ada dan HyDE tak mudah 'melenceng' untuk pertanyaan pendek.
"""
import os
import threading

try:
    import llm_client
except Exception:
    llm_client = None


_SYS_HYDE = (
    "Anda ahli hukum perpajakan Indonesia. Tuliskan SATU paragraf singkat "
    "(maksimal 3 kalimat) berisi diksi seperti bunyi PASAL peraturan perpajakan "
    "yang paling mungkin menjawab pertanyaan pengguna. Gunakan gaya formal-"
    "normatif (istilah baku: Wajib Pajak, Direktur Jenderal Pajak, Surat "
    "Pemberitahuan, sebagaimana dimaksud, dan seterusnya). JANGAN memberi "
    "pengantar. JANGAN menyebut nomor pasal/peraturan spesifik bila tidak yakin; "
    "cukup tuliskan substansi ketentuannya. Jawab hanya paragraf itu."
)


def _enabled():
    v = os.environ.get("RAG_HYDE")
    if v is None:
        return False
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _max_tokens():
    try:
        return int(os.environ.get("RAG_HYDE_MAX_TOKENS", "160"))
    except Exception:
        return 160


def _min_len():
    # Pertanyaan sangat pendek (mis. "npwp") sudah cukup jadi query; HyDE malah
    # bisa menyesatkan. Lewati bila lebih pendek dari ambang ini.
    try:
        return int(os.environ.get("RAG_HYDE_MIN_LEN", "12"))
    except Exception:
        return 12


_CACHE = {}
_CACHE_ORDER = []
_CACHE_MAX = 256
_LOCK = threading.Lock()


def _cache_get(key):
    with _LOCK:
        return _CACHE.get(key)


def _cache_put(key, val):
    with _LOCK:
        if key in _CACHE:
            return
        _CACHE[key] = val
        _CACHE_ORDER.append(key)
        if len(_CACHE_ORDER) > _CACHE_MAX:
            old = _CACHE_ORDER.pop(0)
            _CACHE.pop(old, None)


def hypothetical(query):
    """Kembalikan dokumen hipotetis (str) untuk query, atau None bila gagal."""
    q = (query or "").strip()
    if not q or llm_client is None:
        return None
    if len(q) < _min_len():
        return None
    cached = _cache_get(q)
    if cached is not None:
        return cached or None
    doc = None
    try:
        out = llm_client.chat(
            [{"role": "user", "content": q}],
            system=_SYS_HYDE,
            max_new_tokens=_max_tokens(),
            temperature=0.3,
        )
        doc = (out or "").strip()
    except Exception:
        doc = None
    _cache_put(q, doc or "")   # cache kegagalan (string kosong) agar tak diulang
    return doc or None


def untuk_dense(query):
    """Query untuk embedding DENSE.

    Bila HyDE aktif & berhasil -> 'query asli + dokumen hipotetis'. Selain itu
    -> query asli (fail-open). Jalur LEXICAL/FTS TIDAK memakai fungsi ini.
    """
    q = (query or "").strip()
    if not _enabled():
        return query
    doc = hypothetical(q)
    if not doc:
        return query
    return (q + "\n" + doc).strip()
