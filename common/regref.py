# -*- coding: utf-8 -*-
"""regref.py — Deteksi & resolusi rujukan peraturan dari teks bebas (Fase 5).

Mengenali berbagai format penulisan rujukan yang muncul di jawaban historis
(livechat/Sosmed), mis.:

  PMK 10/2025            PMK No 10 TH 2025          PMK Nomor 10 Tahun 2025
  PMK-10/PMK.03/2025     PER-23/PJ/2025             PP 41 TAHUN 2021
  UU No. 36 Tahun 2008   SE-05/PJ/2024              KMK 123/KMK.04/2020

Tiap deteksi dinormalisasi menjadi (jenis, nomor_utama, sub_kode, tahun), lalu
diresolusi ke peraturan_unit (tabel rapi: jenis_peraturan, nomor, tahun) dengan
peta tercache. Bila beberapa dokumen cocok, dipilih yang berstatus 'berlaku'
lalu yang terbaru; hasil None bila tak ada yang cocok (jangan menebak).

Dipakai qa_index_db (saat build) & rag_qa_patch (label saat query).
Gagal-anggun penuh; tanpa dependensi eksternal.
"""
import re

_JENIS_TUPLE = ("PERPU", "PERPRES", "PMK", "PER", "PP", "UU", "KMK", "KEP", "SE")
_JENIS_SET = set(_JENIS_TUPLE)

# jenis + [Nomor/No] + angka utama + [sub-kode /XXX atau .XX maks 2x] +
# [Tahun/TH] + tahun. Sub-kode HANYA bila ada pemisah tegas (/ atau .) agar
# "PMK 10 Tahun 2025" tidak menelan kata TAHUN sebagai sub-kode.
_RE = re.compile(
    r"\b(PERPU|PERPRES|PMK|PER|PP|UU|KMK|KEP|SE)\b"
    r"[\s.\-]*"
    r"(?:Nomor|NOMOR|No\.?|nomor)?[\s.\-]*"
    r"(\d{1,3}[A-Za-z]?)"
    r"((?:\s*[/.]\s*[A-Z]{1,6}(?:\.\d{1,2})?){0,2})"
    r"[\s./\-]*"
    r"(?:Tahun|TAHUN|TH|Th|tahun|th)?[\s./\-]*"
    r"((?:19|20)\d{2})\b"
)

_RE_FIRST_NUM = re.compile(r"(\d{1,3})")


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def detect(text):
    """Daftar rujukan mentah yang terdeteksi di teks:
    [{jenis, num, sub, tahun, raw}] — urut kemunculan, tanpa duplikat."""
    out, seen = [], set()
    for m in _RE.finditer(text or ""):
        jenis = m.group(1).upper()
        num = m.group(2).upper()
        sub = re.sub(r"\s+", "", m.group(3) or "").upper()
        tahun = m.group(4)
        key = (jenis, num, sub, tahun)
        if key in seen:
            continue
        seen.add(key)
        out.append({"jenis": jenis, "num": num, "sub": sub, "tahun": tahun,
                    "raw": m.group(0).strip()})
    return out


# --------------------------------------------------------------- resolusi DB
_CACHE = {"sig": None, "rows": []}


def _load(conn):
    """Muat (cached) daftar identitas dokumen dari peraturan_unit."""
    try:
        sig = conn.execute(
            "SELECT COUNT(DISTINCT source_id) FROM peraturan_unit").fetchone()[0]
    except Exception:
        sig = -1
    if _CACHE["sig"] == sig and _CACHE["rows"]:
        return _CACHE["rows"]
    rows = []
    try:
        for r in conn.execute(
                "SELECT jenis_peraturan, nomor, tahun, judul, status, source_id "
                "FROM peraturan_unit WHERE nomor IS NOT NULL "
                "GROUP BY jenis_peraturan, nomor").fetchall():
            nomor = str(r["nomor"] or "")
            mn = _RE_FIRST_NUM.search(nomor)
            rows.append({
                "jenis": str(r["jenis_peraturan"] or "").upper(),
                "nomor": nomor,
                "nomor_norm": _norm(nomor),
                "num": mn.group(1) if mn else "",
                "tahun": str(r["tahun"] or ""),
                "judul": str(r["judul"] or ""),
                "status": str(r["status"] or ""),
                "source_id": str(r["source_id"] or ""),
            })
    except Exception:
        rows = []
    _CACHE["sig"] = sig
    _CACHE["rows"] = rows
    return rows


def resolve(conn, jenis, num, sub, tahun):
    """Resolusi satu rujukan -> dict dokumen terbaik atau None.

    Urutan kecocokan: (jenis + tahun + nomor utama) -> (tahun + nomor utama,
    jenis apa pun). Sub-kode & status 'berlaku' jadi penentu prioritas."""
    rows = _load(conn)
    if not rows or not num or not tahun:
        return None
    j = (jenis or "").upper()
    num_key = _norm(num)
    t = str(tahun)

    def _skor(r):
        s = 0
        if r["num"] == num or (num_key and num_key in r["nomor_norm"]):
            s += 4
        else:
            return -1
        if r["tahun"] == t or t in r["nomor_norm"]:
            s += 2
        else:
            return -1
        if j and r["jenis"] == j:
            s += 2
        if sub and _norm(sub) and _norm(sub) in r["nomor_norm"]:
            s += 1
        if r["status"] == "berlaku":
            s += 1
        return s

    best, best_s = None, 0
    for r in rows:
        s = _skor(r)
        if s > best_s:
            best, best_s = r, s
    return best


def detect_resolve(text, conn):
    """Deteksi + resolusi sekaligus. Kembalikan list deteksi yang diperkaya:
    tiap item punya 'match' (dict dokumen atau None) dan 'label' tampilan."""
    out = []
    for d in detect(text):
        lab = "%s %s%s %s" % (d["jenis"], d["num"],
                              ("/" + d["sub"].lstrip("/")) if d["sub"] else "",
                              d["tahun"])
        d["label"] = re.sub(r"\s+", " ", lab).strip()
        try:
            d["match"] = resolve(conn, d["jenis"], d["num"], d["sub"], d["tahun"])
        except Exception:
            d["match"] = None
        out.append(d)
    return out
