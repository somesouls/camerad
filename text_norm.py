# -*- coding: utf-8 -*-
"""text_norm.py — Normalisasi teks Bahasa Indonesia untuk lexical retrieval (Fase 1).

Masalah: tokenizer FTS5 unicode61 memecah kata tapi TIDAK menyamakan bentuk
berimbuhan ("penyerahan" vs "menyerahkan" vs "diserahkan") dan tidak membuang
kata fungsi, sehingga query informal sering meleset dari bahasa baku peraturan.

Modul ini menormalisasi teks SEBELUM diindeks maupun saat query:

  1. lowercase + buang diakritik (NFKD) + non-alfanumerik -> spasi
  2. buang stopword Bahasa Indonesia
  3. stemming Sastrawi per token (opsional; env RAG_NORM_STEM=1 default aktif;
     bila paket Sastrawi belum terpasang -> stemming dilewati, normalisasi
     dasar tetap jalan)

Dipakai peraturan_db / sop_db (FTS v2: konten ternormalisasi + bm25 berbobot)
dan rag_sources_patch (skor leksikal AWE/Sosmed). Gagal-anggun penuh.
"""
import os
import re
import unicodedata

# Stopword Bahasa Indonesia (kata fungsi/umum; sengaja ringkas agar aman).
_STOP = set("""
yang dan di ke dari untuk pada dengan atau ini itu ada apa adalah akan tentang
bagaimana gimana kenapa mengapa kah min admin kak pak bu mohon tolong ya nya
saya aku kami kita mau ingin bisa tidak gak ga nggak sudah belum juga kalau jika
saja lagi kok dong sih halo hai cara dalam oleh sebagai agar supaya telah antara
bagi namun tetapi serta ialah yaitu yakni demi atas bawah setiap para sang si
pun lah tah per se secara lebih paling sangat amat tersebut menjadi jadi karena
sebab maka bilamana bila ketika saat seraya sambil meski meskipun walaupun
kendati sungguhpun biar guna demikian begitu seperti ibarat bagaikan laksana
alih ketimbang the a an is to of for in on at by we you he she they it our your
dst dsb dll dkk kalo klo utk yg gmn tiap sesuatu siapapun apapun kapanpun
dimanapun manapun bagaimanapun berapapun sampai hingga sejak selama sebelum
sesudah setelah sewaktu waktu tatkala manakala senyampang
""".split())

_MIN_LEN = 3
_RE_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_STEMMER = None
_STEM_TRIED = False


def stem_enabled():
    return str(os.environ.get("RAG_NORM_STEM", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _get_stemmer():
    global _STEMMER, _STEM_TRIED
    if _STEMMER is not None:
        return _STEMMER
    if _STEM_TRIED:
        return None
    _STEM_TRIED = True
    if not stem_enabled():
        return None
    try:
        from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
        _STEMMER = StemmerFactory().create_stemmer()
        print("[text_norm] stemming Sastrawi aktif.", flush=True)
    except Exception:
        _STEMMER = None
    return _STEMMER


def _base(text):
    """lowercase + buang diakritik + non-alfanumerik -> spasi."""
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return _RE_NON_ALNUM.sub(" ", t)


def tokens(text, stopword=True, stem=None, min_len=_MIN_LEN):
    """Daftar token ternormalisasi (urutan dipertahankan)."""
    out = []
    for w in _base(text).split():
        if len(w) < min_len and not w.isdigit():
            continue
        if stopword and w in _STOP:
            continue
        out.append(w)
    if stem is None:
        stem = stem_enabled()
    if stem:
        st = _get_stemmer()
        if st is not None:
            try:
                out = [st.stem(w) for w in out]
            except Exception:
                pass
    return out


def normalize(text):
    """Teks ternormalisasi — untuk disimpan di FTS / dibandingkan."""
    return " ".join(tokens(text))


def norm_tokens(text, k=0):
    """Token unik terurut (untuk query/skor); k>0 membatasi jumlah."""
    seen, out = set(), []
    for w in tokens(text):
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if k and len(out) >= k:
            break
    return out


def info():
    """Diagnosis singkat (dipakai phase1_upgrade)."""
    return {"stem_enabled": stem_enabled(),
            "sastrawi": _get_stemmer() is not None,
            "stopwords": len(_STOP)}
