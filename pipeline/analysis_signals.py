# -*- coding: utf-8 -*-
"""analysis_signals.py — Sinyal bantu analisis manual Step 6 (Fallback) & Step 9 (MKTA).

Menghasilkan "acuan" ringkas per pertanyaan user agar analis cepat menilai:
- panjang / kalimat majemuk / multi-topik (indikasi "lebih dari 1 konteks"),
- istilah pajak terdeteksi (glosarium) + pemicu ambiguitas (disambiguasi),
- ada rujukan nomor peraturan, akronim,
- (Step 9) beda putusan mesin vs LLM, dan ambiguitas antar kandidat (Step 6).

DRY: dipakai oleh pipeline/step6_patch.py & pipeline/step9_patch.py.
FAIL-OPEN: semua dibungkus try/except; bila glosarium/disambiguasi/DB tak
tersedia, sinyal tetap dikembalikan (bagian yang gagal dikosongkan) tanpa
mengganggu pemuatan tabel.
"""
import re

# Konjungsi/penanda yang sering muncul pada pertanyaan majemuk (>1 topik).
_KONJUNGSI = [
    "dan", "atau", "lalu", "kemudian", "terus", "juga", "serta", "sekaligus",
    "selain itu", "tetapi", "tapi", "namun", "sedangkan", "sambil",
]
# Rujukan nomor peraturan: PER/PMK/PP/UU/KEP/SE/PERPU/PERPRES/PERDIRJEN + angka.
_RE_PERATURAN = re.compile(
    r"\b(per|pmk|pp|uu|kep|se|perpu|perpres|perdirjen|kmk)\b[\s./-]*\d",
    re.IGNORECASE,
)
# Akronim huruf kapital 2-6 (NPWP, EFIN, SPT, PPN, KTP, DJP, AHU, ...).
_RE_AKRONIM = re.compile(r"\b[A-Z]{2,6}\b")

AMBANG_KATA_PANJANG = 20
AMBANG_CHAR_PANJANG = 140
AMBANG_KATA_MAJEMUK = 12
AMBANG_GAP_KANDIDAT = 0.10  # gap skor kandidat #1-#2 <= 10% => mirip/ambigu


def _tokens(s):
    return [t for t in re.split(r"\W+", (s or "").lower()) if t]


def _num(v):
    """Angka toleran: '89%' / '0,89' / '0.89' -> float 0..1 (perkiraan)."""
    try:
        if v is None:
            return None
        s = str(v).strip().replace("%", "").replace(",", ".")
        if s == "":
            return None
        f = float(s)
        if f > 1.5:  # kemungkinan skala 0-100 -> 0-1
            f = f / 100.0
        return f
    except Exception:
        return None


def _skor_options(options):
    out = []
    for o in (options or []):
        if isinstance(o, dict):
            v = _num(o.get("skor"))
            if v is not None:
                out.append(v)
    out.sort(reverse=True)
    return out


def open_conn(gdb=None, ddb=None):
    """Buka koneksi basis pengetahuan (glosarium+disambiguasi). None bila gagal.

    Keduanya berbagi satu database analitik, jadi cukup satu koneksi.
    """
    if gdb is None:
        return None
    try:
        conn = gdb.connect()
        try:
            gdb.init_db(conn)
        except Exception:
            pass
        if ddb is not None:
            try:
                ddb.init_db(conn)
            except Exception:
                pass
        return conn
    except Exception:
        return None


def hitung_sinyal(teks, options=None, intent_mesin=None, intent_llm=None,
                  conn=None, gdb=None, ddb=None, tanggal=None):
    """Kembalikan dict sinyal untuk satu pertanyaan user."""
    s = {
        "panjang_kata": 0, "panjang_char": 0, "is_panjang": False,
        "is_majemuk": False, "penanda_majemuk": [],
        "glosarium": [], "has_istilah": False,
        "disambig": [], "is_ambigu": False,
        "is_multi_topik": False,
        "has_no_peraturan": False, "akronim": [],
        "beda_llm": False,
        "ambiguitas_kandidat": False, "gap_kandidat": None,
        "badges": [],
    }
    try:
        teks = teks or ""
        low = teks.lower()
        toks = _tokens(teks)
        s["panjang_kata"] = len(toks)
        s["panjang_char"] = len(teks)
        s["is_panjang"] = bool(len(toks) >= AMBANG_KATA_PANJANG
                               or len(teks) >= AMBANG_CHAR_PANJANG)

        pad = " " + low + " "
        penanda = [k for k in _KONJUNGSI if (" " + k + " ") in pad]
        s["penanda_majemuk"] = penanda
        n_tanya = teks.count("?")
        n_kalimat = len([x for x in re.split(r"[.!?\n]+", teks) if x.strip()])
        s["is_majemuk"] = bool(
            n_tanya >= 2
            or (len(toks) >= AMBANG_KATA_MAJEMUK and penanda)
            or n_kalimat >= 3
        )

        s["has_no_peraturan"] = bool(_RE_PERATURAN.search(teks))
        s["akronim"] = sorted(set(_RE_AKRONIM.findall(teks)))

        kategori_glos = set()
        if conn is not None and gdb is not None:
            try:
                g = gdb.match(conn, teks, limit=5) or []
                s["glosarium"] = [x.get("term") for x in g if x.get("term")]
                for x in g:
                    if x.get("kategori"):
                        kategori_glos.add(x.get("kategori"))
                s["has_istilah"] = bool(g)
            except Exception:
                pass
        if conn is not None and ddb is not None:
            try:
                d = ddb.match(conn, teks, tanggal=tanggal, limit=5) or []
                s["dis