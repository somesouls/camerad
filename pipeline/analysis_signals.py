# -*- coding: utf-8 -*-
"""Sinyal bantu analisis manual Step 6 (Fallback) & Step 9 (MKTA).
Dipakai step6_patch.py & step9_signals_patch.py. Fail-open (semua try/except)."""
import re

_KONJUNGSI = ["dan", "atau", "lalu", "kemudian", "terus", "juga", "serta",
              "sekaligus", "selain itu", "tetapi", "tapi", "namun",
              "sedangkan", "sambil"]
_RE_PERATURAN = re.compile(
    r"\b(per|pmk|pp|uu|kep|se|perpu|perpres|perdirjen|kmk)\b[\s./-]*\d", re.I)
_RE_AKRONIM = re.compile(r"\b[A-Z]{2,6}\b")

KATA_PANJANG = 20
CHAR_PANJANG = 140
KATA_MAJEMUK = 12
GAP_KANDIDAT = 0.10


def _num(v):
    try:
        s = str("" if v is None else v).strip().replace("%", "").replace(",", ".")
        if not s:
            return None
        f = float(s)
        return f / 100.0 if f > 1.5 else f
    except Exception:
        return None


def open_conn(gdb=None, ddb=None):
    """Satu koneksi utk glosarium+disambiguasi (berbagi DB). None bila gagal."""
    if gdb is None:
        return None
    try:
        conn = gdb.connect()
        for m in (gdb, ddb):
            try:
                if m is not None:
                    m.init_db(conn)
            except Exception:
                pass
        return conn
    except Exception:
        return None


def hitung_sinyal(teks, options=None, intent_mesin=None, intent_llm=None,
                  conn=None, gdb=None, ddb=None, tanggal=None):
    s = {"panjang_kata": 0, "panjang_char": 0, "is_panjang": False,
         "is_majemuk": False, "penanda_majemuk": [], "glosarium": [],
         "has_istilah": False, "disambig": [], "is_ambigu": False,
         "is_multi_topik": False, "has_no_peraturan": False, "akronim": [],
         "beda_llm": False, "ambiguitas_kandidat": False, "gap_kandidat": None,
         "badges": []}
    try:
        teks = teks or ""
        low = " " + teks.lower() + " "
        toks = [t for t in re.split(r"\W+", teks.lower()) if t]
        s["panjang_kata"] = len(toks)
        s["panjang_char"] = len(teks)
        s["is_panjang"] = bool(len(toks) >= KATA_PANJANG or len(teks) >= CHAR_PANJANG)
        penanda = [k for k in _KONJUNGSI if (" " + k + " ") in low]
        s["penanda_majemuk"] = penanda
        n_kalimat = len([x for x in re.split(r"[.!?\n]+", teks) if x.strip()])
        s["is_majemuk"] = bool(teks.count("?") >= 2
                               or (len(toks) >= KATA_MAJEMUK and penanda)
                               or n_kalimat >= 3)
        s["has_no_peraturan"] = bool(_RE_PERATURAN.search(teks))
        s["akronim"] = sorted(set(_RE_AKRONIM.findall(teks)))
        kat = set()
        if conn is not None and gdb is not None:
            try:
                g = gdb.match(conn, teks, limit=5) or []
                s["glosarium"] = [x.get("term") for x in g if x.get("term")]
                kat = {x.get("kategori") for x in g if x.get("kategori")}
                s["has_istilah"] = bool(g)
            except Exception:
                pass
        if conn is not None and ddb is not None:
            try:
                d = ddb.match(conn, teks, tanggal=tanggal, limit=5) or []
                s["disambig"] = [x.get("pemicu") for x in d if x.get("pemicu")]
                s["is_ambigu"] = bool(d)
            except Exception:
                pass
        s["is_multi_topik"] = bool(len(s["disambig"]) >= 2 or len(kat) >= 2
                                   or (s["is_majemuk"] and s["has_istilah"]))
        if intent_mesin and intent_llm:
            s["beda_llm"] = str(intent_mesin).strip() != str(intent_llm).strip()
        sk = sorted([_num(o.get("skor")) for o in (options or [])
                     if isinstance(o, dict) and _num(o.get("skor")) is not None],
                    reverse=True)
        if len(sk) >= 2:
            s["gap_kandidat"] = round(sk[0] - sk[1], 4)
            s["ambiguitas_kandidat"] = bool(s["gap_kandidat"] <= GAP_KANDIDAT)
        pairs = [("is_panjang", "panjang"), ("is_majemuk", "majemuk"),
                 ("is_multi_topik", "multi-topik"), ("has_istilah", "istilah"),
                 ("is_ambigu", "ambigu"), ("ambiguitas_kandidat", "kandidat-mirip"),
                 ("has_no_peraturan", "no.peraturan"), ("beda_llm", "beda-LLM")]
        s["badges"] = [lbl for key, lbl in pairs if s[key]]
    except Exception:
        pass
    return s
