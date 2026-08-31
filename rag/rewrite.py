# -*- coding: utf-8 -*-
"""rag_rewrite.py — Query rewriting + kamus sinonim untuk retrieval RAG (Tahap 5).

Menjembatani "vocabulary mismatch": pertanyaan awam pengguna sering jauh dari
bahasa hukum/formal pada korpus peraturan. Modul ini memperkaya query SEBELUM
retrieval hybrid (dipakai lewat rag_rerank_patch yang membungkus
peraturan_db.search):

  1. expand_kamus(q)  -> tambah istilah baku/sinonim dari rag_kamus_db.
  2. varian_baku(q)   -> REWRITE DUA-ARAH DETERMINISTIK (non-LLM): ubah gaya
                         kolokial/akronim menjadi istilah baku pajak (mis.
                         "pkp itu apa sih" -> "pkp apa Pengusaha Kena Pajak";
                         "batas daftar NPWP PT baru berdiri" -> tambah
                         "jangka waktu pendaftaran Nomor Pokok Wajib Pajak
                         Perseroan Terbatas Wajib Pajak Badan yang baru
                         didirikan"). Tetap jalan saat AI-rewrite dimatikan
                         (RAG_REWRITE_AI=0) maupun di eval (rewrite_ai=0).
  3. rewrite_ai(q)    -> FITUR REWRITING OTOMATIS AI: mengubah pertanyaan awam
                         menjadi query hukum formal + menebak jenis peraturan /
                         pasal / istilah terkait (hanya sebagai PETUNJUK
                         pencarian, BUKAN fakta jawaban).
  4. untuk_retrieval(q) -> gabungan query efektif (q + baku deterministik +
                          kamus + istilah formal AI) yang dipakai untuk
                          retrieval. Rerank & gerbang cosine tetap memakai
                          query ASLI.
  5. varian_kueri(q)  -> [q_asli, q_baku] (deduplikasi) untuk penilai gerbang
                          yang ingin menilai cosine MAKS lintas-varian.
                          Deterministik; dikonsumsi rag_calibration_patch pada
                          langkah penyambungan gerbang (menyusul).

AI rewriting hanya dijalankan untuk pertanyaan bergaya natural (bukan sekadar
kutipan nomor peraturan) dan hasilnya di-cache per-query agar tidak memanggil
LLM berkali-kali dalam satu jawaban.

Konfigurasi (env):
  RAG_REWRITE_AI            '1' (default) aktif; '0' matikan (kamus + rewrite
                            dua-arah deterministik tetap jalan).
  RAG_REWRITE_AI_PROFILES   daftar profil (dipisah koma) yang boleh AI-rewrite;
                            kosong = semua boleh. Mis. 'agent' -> chatbot dilewati.
  RAG_TWOWAY_REWRITE        '1' (default) aktif rewrite dua-arah deterministik;
                            '0' matikan (kembali ke perilaku lama: q + kamus + AI).
Gagal-anggun: setiap kegagalan -> kembali ke query asli/tanpa rewrite.
"""
import os
import re
import json
import contextvars

try:
    import common.llm_client as llm_client
except Exception:            # pragma: no cover
    llm_client = None
try:
    import rag.kamus_db as kdb
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


def _twoway_enabled():
    """True bila rewrite dua-arah deterministik aktif (default ON)."""
    return str(os.environ.get("RAG_TWOWAY_REWRITE", "1")).strip().lower() not in (
        "0", "false", "no", "off")


# Konteks per-request (aman untuk banyak request paralel via contextvars):
# profil aktif & apakah profil 'cepat'. Diisi oleh rag_engine.answer() sebelum
# retrieval, lalu dibaca saat memutuskan apakah AI-rewrite dijalankan.
_CTX_PROFILE = contextvars.ContextVar("rag_rw_profile", default="")
_CTX_FAST = contextvars.ContextVar("rag_rw_fast", default=False)


def set_context(profile_id=None, fast=None):
    """Tandai profil aktif (+ apakah 'cepat') untuk request saat ini."""
    try:
        if profile_id is not None:
            _CTX_PROFILE.set(str(profile_id or "").strip().lower())
        if fast is not None:
            _CTX_FAST.set(bool(fast))
    except Exception:
        pass


def set_active_profile(profile_id=None):
    """Kompatibilitas: set profil aktif saja."""
    set_context(profile_id=profile_id)


def _ai_profile_allowed():
    """True bila AI-rewrite boleh untuk konteks saat ini.

    Prioritas:
      1. env RAG_REWRITE_AI_PROFILES (daftar profil dipisah koma) -> hanya profil
         tsb yang boleh (eksplisit, menang atas apa pun).
      2. Profil aktif ditandai 'cepat' -> AI-rewrite dilewati (chatbot ngebut).
      3. Selain itu -> boleh (kompatibel mundur).
    """
    raw = str(os.environ.get("RAG_REWRITE_AI_PROFILES", "")).strip()
    try:
        pid = _CTX_PROFILE.get()
    except Exception:
        pid = ""
    if raw:
        allow = {x.strip().lower() for x in raw.split(",") if x.strip()}
        if not allow or not pid:
            return True
        return pid in allow
    try:
        if _CTX_FAST.get():
            return False
    except Exception:
        pass
    return True


def expand_kamus(q):
    """Daftar istilah tambahan dari kamus sinonim (list[str])."""
    if kdb is None:
        return []
    try:
        return kdb.expand_terms(q)
    except Exception:
        return []


# ==========================================================================
# Rewrite DUA-ARAH deterministik (non-LLM) — kolokial/akronim -> baku pajak.
# Bekerja tanpa LLM, jadi tetap aktif pada mode 'cepat', RAG_REWRITE_AI=0,
# maupun evaluasi (phase4_eval rewrite_ai=0). Semua bersifat ADITIF: istilah
# baku ditambahkan, tidak menghapus makna asli, sehingga aman (gagal-anggun).
# ==========================================================================

# Akronim/istilah pendek -> frasa baku. Dicocokkan sebagai KATA UTUH (\b).
_ISTILAH_BAKU = {
    "pkp": "Pengusaha Kena Pajak",
    "bkp": "Barang Kena Pajak",
    "jkp": "Jasa Kena Pajak",
    "npwp": "Nomor Pokok Wajib Pajak",
    "nppn": "Norma Penghitungan Penghasilan Neto",
    "spln": "Subjek Pajak Luar Negeri",
    "spdn": "Subjek Pajak Dalam Negeri",
    "wpln": "Wajib Pajak Luar Negeri",
    "wpdn": "Wajib Pajak Dalam Negeri",
    "wpop": "Wajib Pajak Orang Pribadi",
    "pt": "Perseroan Terbatas Wajib Pajak Badan",
    "ppn": "Pajak Pertambahan Nilai",
    "pph": "Pajak Penghasilan",
    "pbb": "Pajak Bumi dan Bangunan",
    "ptkp": "Penghasilan Tidak Kena Pajak",
    "njop": "Nilai Jual Objek Pajak",
    "njoptkp": "Nilai Jual Objek Pajak Tidak Kena Pajak",
    "skb": "Surat Keterangan Bebas",
    "sktd": "Surat Keterangan Tidak Dipungut",
    "skd": "Surat Keterangan Domisili",
    "spt": "Surat Pemberitahuan",
    "efin": "Electronic Filing Identification Number",
    "ikn": "Ibu Kota Nusantara",
    "umkm": "Usaha Mikro Kecil dan Menengah",
    "p3b": "Persetujuan Penghindaran Pajak Berganda",
}
_RE_AKRONIM = re.compile(
    r"\b(" + "|".join(
        sorted((re.escape(k) for k in _ISTILAH_BAKU), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

# Frasa kolokial -> frasa baku (substring, case-insensitive). Aditif.
_FRASA_BAKU = (
    ("keringanan pajak", "fasilitas pengurangan pajak"),
    ("batas daftar", "jangka waktu pendaftaran"),
    ("batas setor", "jangka waktu penyetoran"),
    ("batas bayar", "jangka waktu penyetoran"),
    ("batas lapor", "jangka waktu pelaporan"),
    ("telat lapor", "terlambat menyampaikan"),
    ("telat bayar", "terlambat menyetor"),
    ("denda telat", "sanksi administrasi keterlambatan"),
    ("baru berdiri", "yang baru didirikan"),
    ("baru bikin", "yang baru didirikan"),
    ("daftar npwp", "pendaftaran Nomor Pokok Wajib Pajak"),
    ("peralihan", "ketentuan peralihan"),
    ("bedanya", "perbedaan"),
)

# Kata pengisi kolokial yang dibuang pada varian baku (tak mengubah makna).
_FILLER = set("""sih dong kok nih deh tuh min kak pak bu bang gan aja doang bgt
banget gitu gtu loh lho ya yaa yah dah nya kan woy bro sis ges gaes""".split())

# Normalisasi ejaan singkat kolokial -> baku.
_EJAAN = {
    "gmn": "bagaimana", "gmna": "bagaimana", "gimana": "bagaimana",
    "klo": "kalau", "kalo": "kalau", "utk": "untuk", "yg": "yang",
    "dgn": "dengan", "tdk": "tidak", "gak": "tidak", "ga": "tidak",
    "nggak": "tidak", "brp": "berapa", "dr": "dari", "krn": "karena",
    "bikin": "membuat", "buat": "untuk",
}


def _strip_normalize(q):
    """Buang kata pengisi kolokial + normalisasi ejaan singkat. Aman & cepat."""
    out = []
    for w in re.findall(r"[A-Za-z0-9/\-]+", q or ""):
        lw = w.lower()
        if lw in _FILLER:
            continue
        out.append(_EJAAN.get(lw, w))
    return " ".join(out)


def varian_baku(q):
    """Varian BAKU deterministik dari query kolokial (string, non-LLM).

    = query (setelah buang filler + normalisasi ejaan) + frasa baku hasil
    ekspansi akronim/frasa. ADITIF: tak menghapus makna asli. Gagal-anggun ->
    string kosong bila input kosong/tak ada tambahan.
    """
    q = (q or "").strip()
    if not q:
        return ""
    base = _strip_normalize(q)
    base_l = base.lower()
    extra = []
    for m in _RE_AKRONIM.findall(q):
        baku = _ISTILAH_BAKU.get(str(m).lower())
        if baku and baku.lower() not in base_l and baku not in extra:
            extra.append(baku)
    ql = q.lower()
    for src, dst in _FRASA_BAKU:
        if src in ql and dst.lower() not in base_l and dst not in extra:
            extra.append(dst)
    extra = extra[:8]
    v = (base + (" " + " ".join(extra) if extra else "")).strip()
    return v


def varian_kueri(q):
    """Daftar varian kueri untuk penilai gerbang cosine MAKS lintas-varian.

    Kembalikan [q_asli] bila rewrite dua-arah nonaktif / tak ada varian baru,
    atau [q_asli, q_baku] (deduplikasi case-insensitive). Dikonsumsi
    rag_calibration_patch untuk menilai cosine terbaik antar-varian sehingga
    kueri sah bergaya kolokial tidak keburu digerbang (false-abstain), tanpa
    menurunkan ambang bagi kueri luar-domain (OOD tak punya padanan baku).
    Deterministik & gagal-anggun.
    """
    q = (q or "").strip()
    if not q:
        return []
    out = [q]
    if not _twoway_enabled():
        return out
    try:
        v = varian_baku(q)
    except Exception:
        v = ""
    if v and v.lower() != q.lower():
        out.append(v)
    return out


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
        if not _ai_profile_allowed():
            base["alasan"] = "AI rewriting dilewati untuk profil ini (profil cepat / RAG_REWRITE_AI_PROFILES)."
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


def untuk_retrieval(q):
    """Query efektif untuk retrieval = q + baku deterministik + kamus + istilah
    formal AI.

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

    # (0) Rewrite dua-arah deterministik (non-LLM) — aktif walau AI dimatikan.
    if _twoway_enabled():
        try:
            _add(varian_baku(q))
        except Exception:
            pass
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
