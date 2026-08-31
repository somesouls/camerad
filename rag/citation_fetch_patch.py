# -*- coding: utf-8 -*-
"""rag/citation_fetch_patch.py — fetch sitasi eksplisit nomor+pasal (Tahap 4f).

Masalah yang diperbaiki: query sitasi seperti "isi Pasal 40 PMK 81 Tahun 2024"
bisa ABSTAIN walau unit-nya ADA di DB. Tiga penyebab bertumpuk di jalur
peraturan:
  1. filter status keras di peraturan.db.search (default hanya 'berlaku') ->
     unit berstatus 'diubah'/'dicabut' dibuang sebelum sempat muncul.
  2. successor_patch menekan ISI unit usang (hanya menambah CATATAN + menarik
     penerus), jadi ISI pasal yang DIMINTA tak pernah tampil.
  3. gerbang cosine RAG_MIN_COS (mis. 0.61 profil agent) membuang unit karena
     query sitasi mirip-metadata -> cosine rendah vs body pasal.

Patch ini memasang lapis TERLUAR pada _ctx_peraturan. BILA query menyebut nomor
peraturan + pasal EKSPLISIT: tarik unit persis via SQL mentah LINTAS SEMUA
STATUS (kebal FTS/vektor/gerbang), tampilkan ISI-nya + penanda status + pointer
pengubah SADAR-PASAL (Tahap 4f-1: cari peraturan pengubah INDUK lewat pola judul
"NOMOR <n> TAHUN <t>", tandai yang menyebut pasal ini secara spesifik; fallback
redaksi aman yang TIDAK mengklaim pengganti tanpa verifikasi). Query tanpa sitasi
nomor+pasal -> passthrough ke perilaku lama (nol dampak).

Knob RAG_CITATION_FETCH per-profil via rag.knob_store (store>env>default True),
pola sama dgn rag.nomor_pin_patch. Dipasang lewat web_app.py SESUDAH
rag.drilldown_patch (yang membungkus _ctx_peraturan terakhir) supaya jalur
normal (delegasi _prev) tetap memakai successor+validity+drilldown yang ada.
Gagal-anggun penuh: error apa pun -> jalur lama. Tanpa f-string (gaya repo).

Uji cepat mandiri:
    python -m rag.citation_fetch_patch "isi Pasal 40 PMK 81 Tahun 2024"
"""
import os as _os
import re as _re_std

import rag.engine as _re

try:
    import peraturan.db as _pdb
except Exception:            # pragma: no cover
    _pdb = None

try:
    import rag.knob_store as _ks
except Exception:            # pragma: no cover
    _ks = None

try:
    import rag.calibration as _cal
except Exception:            # pragma: no cover
    _cal = None

try:
    from rag.nomor_pin_patch import _RE_NOMOR as _RE_NOMOR
except Exception:            # pragma: no cover
    _RE_NOMOR = _re_std.compile(
        r"(?:PER|SE|KEP|PENG|KMK)\s*-?\s*\d+\s*/\s*PJ(?:\.\w+)?\s*/\s*\d{4}"
        r"|\d+\s*/\s*PMK\.?\s*\d*\s*/\s*\d{4}"
        r"|(?:PMK|KMK|PP|UU|PERPU|PERPPU|PERMENKEU)\s*-?\s*\d+\s*(?:TAHUN\s*)?/?\s*\d{4}",
        _re_std.IGNORECASE)

# nomor + pasal EKSPLISIT: "pasal 40", "Pasal 40A", "pasal40".
_RE_PASAL = _re_std.compile(r"pasal\s*(\d+[a-z]?)", _re_std.IGNORECASE)
_RE_JENIS = _re_std.compile(
    r"(PERMENKEU|PERPPU|PERPU|PMK|KMK|KEP|PENG|PER|SE|PP|UU)", _re_std.IGNORECASE)
_RE_TAHUN = _re_std.compile(r"(?:19|20)\d{2}")
_RE_ANGKA = _re_std.compile(r"\d+")
# verba perubahan utk deteksi pasal SPESIFIK di teks peraturan pengubah.
_RE_UBAH = _re_std.compile(r"diubah|dihapus|disisip|diganti|dicabut",
                           _re_std.IGNORECASE)

_prev = None  # _ctx_peraturan sebelum patch ini (diisi saat pasang)


def _env_on():
    return str(_os.environ.get("RAG_CITATION_FETCH", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _on():
    """Gate EFEKTIF per-panggilan, PER-PROFIL (store>env>default). Gagal-anggun
    -> _env_on()."""
    if _ks is None:
        return _env_on()
    prof = None
    if _cal is not None:
        try:
            prof = _cal.get_profile()
        except Exception:
            prof = None
    try:
        v = _ks.resolve(prof, "RAG_CITATION_FETCH")
        if v is None:
            return _env_on()
        return bool(v)
    except Exception:
        return _env_on()


def _parse_citation(q):
    """(-> dict|None) Ekstrak jenis/nomor/tahun/pasal bila query menyebut nomor
    peraturan + pasal eksplisit. None bila salah satu tak ada."""
    s = str(q or "")
    mp = _RE_PASAL.search(s)
    if not mp:
        return None
    mn = _RE_NOMOR.search(s)
    if not mn:
        return None
    blob = mn.group(0)
    mj = _RE_JENIS.search(blob)
    jenis = mj.group(1).upper() if mj else ""
    mt = _RE_TAHUN.search(blob)
    tahun = mt.group(0) if mt else ""
    nomor = ""
    for a in _RE_ANGKA.findall(blob):
        if a != tahun:
            nomor = a
            break
    if not nomor:
        return None
    return {"jenis": jenis, "nomor": nomor, "tahun": tahun,
            "pasal": mp.group(1).upper()}


def _rows_to_dicts(rows):
    out = []
    for r in (rows or []):
        try:
            out.append(r if isinstance(r, dict) else dict(r))
        except Exception:
            continue
    return out


def _fetch_units(jenis, nomor, tahun, pasal):
    """Tarik unit persis via SQL mentah, LINTAS SEMUA STATUS. Kebal gate cosine.
    Fallback longgarkan 'jenis' bila nol baris (jaga-jaga normalisasi beda)."""
    if _pdb is None:
        return []
    conn = None
    rows = []
    try:
        conn = _pdb.init_db(_pdb.connect())
        order = (" ORDER BY CASE status WHEN 'berlaku' THEN 0 "
                 "WHEN 'diubah' THEN 1 WHEN 'dicabut' THEN 2 ELSE 3 END LIMIT 20")
        where = ["pasal = ?", "nomor LIKE ?"]
        args = [pasal, "%" + nomor + "%"]
        if jenis:
            where.append("UPPER(jenis_peraturan) = ?")
            args.append(jenis)
        if tahun:
            where.append("CAST(tahun AS TEXT) = ?")
            args.append(str(tahun))
        sql = "SELECT * FROM peraturan_unit WHERE " + " AND ".join(where) + order
        rows = conn.execute(sql, tuple(args)).fetchall()
        if not rows and jenis:
            where2 = ["pasal = ?", "nomor LIKE ?"]
            args2 = [pasal, "%" + nomor + "%"]
            if tahun:
                where2.append("CAST(tahun AS TEXT) = ?")
                args2.append(str(tahun))
            sql2 = ("SELECT * FROM peraturan_unit WHERE "
                    + " AND ".join(where2) + order)
            rows = conn.execute(sql2, tuple(args2)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return _rows_to_dicts(rows)


def _amender_pointer(base_nomor, base_tahun, pasal):
    """Cari peraturan PENGUBAH INDUK secara SADAR-PASAL (Tahap 4f-1).

    Alih-alih trace_successor (relasi dokumen-level yang bisa salah-topik — mis.
    untuk Pasal 40 PMK 81/2024 malah menunjuk PMK 11/2025 soal Nilai Lain PPN),
    telusuri unit yang JUDUL-nya menyebut "NOMOR <base_nomor> TAHUN <base_tahun>"
    dan berstatus 'berlaku' -> itulah peraturan pengubah induk yang sebenarnya
    (PMK 54/2025, PMK 1/2026, dst). Bila ada unit pengubah yang menyebut
    "Pasal <pasal>" + verba perubahan -> tandai sebagai kandidat pengubah
    SPESIFIK pasal ini.

    Kembalikan (spesifik, umum): dua daftar label peraturan (jenis+nomor), unik
    & urut. Gagal-anggun -> ([], []).
    """
    if _pdb is None or not base_nomor or not base_tahun:
        return ([], [])
    conn = None
    rows = []
    try:
        conn = _pdb.init_db(_pdb.connect())
        like = "%NOMOR " + str(base_nomor) + " TAHUN " + str(base_tahun) + "%"
        sql = ("SELECT jenis_peraturan, nomor, tahun, judul, isi, status "
               "FROM peraturan_unit WHERE judul LIKE ? AND status = 'berlaku' "
               "ORDER BY tahun DESC, nomor DESC LIMIT 500")
        rows = conn.execute(sql, (like,)).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    rows = _rows_to_dicts(rows)
    pasal_l = str(pasal or "").strip()
    ref_re = None
    if pasal_l:
        ref_re = _re_std.compile(
            r"pasal\s*0*" + _re_std.escape(pasal_l) + r"(?![0-9a-zA-Z])",
            _re_std.IGNORECASE)
    base_digits = ""
    mb = _RE_ANGKA.search(str(base_nomor))
    if mb:
        base_digits = mb.group(0)
    base_key = (base_digits, str(base_tahun).strip())
    spesifik, umum, seen = [], [], set()
    for d in rows:
        jenis = str(d.get("jenis_peraturan") or "").strip()
        nomor = str(d.get("nomor") or "").strip()
        tahun = str(d.get("tahun") or "").strip()
        num_digits = ""
        mn = _RE_ANGKA.search(nomor)
        if mn:
            num_digits = mn.group(0)
        # lewati induk itu sendiri (nomor+tahun sama dgn yang diminta).
        if (num_digits, tahun) == base_key:
            continue
        reg = " ".join(x for x in [jenis, nomor] if x).strip()
        if not reg:
            continue
        isi = str(d.get("isi") or "")
        is_spesifik = bool(ref_re and ref_re.search(isi) and _RE_UBAH.search(isi))
        if reg not in seen:
            seen.add(reg)
            umum.append(reg)
        if is_spesifik and reg not in spesifik:
            spesifik.append(reg)
    return (spesifik[:3], umum[:5])


def _clip(s, n):
    fn = getattr(_re, "_clip", None)
    try:
        if callable(fn):
            return fn(s, n)
    except Exception:
        pass
    return str(s or "")[:n]


def _head_of(d):
    jenis = str(d.get("jenis_peraturan") or "").strip()
    nomor = str(d.get("nomor") or "").strip()
    pasal = str(d.get("pasal") or "").strip()
    judul = str(d.get("judul") or "").strip()
    tajuk = " ".join(x for x in [jenis, nomor] if x).strip()
    head = tajuk or judul or "Peraturan"
    if pasal:
        head = head + " - Pasal " + pasal
    return head


def _ctx_peraturan_citation(q, limit=4):
    # Passthrough bila patch mati / tak ada delegasi.
    if _prev is None:
        return ("", [])
    if not _on():
        return _prev(q, limit)
    try:
        cit = _parse_citation(q)
    except Exception:
        cit = None
    if not cit:
        return _prev(q, limit)
    try:
        units = _fetch_units(cit["jenis"], cit["nomor"], cit["tahun"], cit["pasal"])
    except Exception:
        units = []
    if not units:
        # Sitasi terdeteksi tapi unit tak ketemu -> jalur lama (jangan memburukkan).
        return _prev(q, limit)

    # Pointer pengubah SADAR-PASAL (Tahap 4f-1): dihitung sekali dari sitasi.
    try:
        sp_regs, um_regs = _amender_pointer(cit.get("nomor"), cit.get("tahun"),
                                            cit.get("pasal"))
    except Exception:
        sp_regs, um_regs = [], []

    blocks, sources, catatan = [], [], []
    seen = set()
    for d in units:
        isi = str(d.get("isi") or "").strip()
        if not isi:
            continue
        head = _head_of(d)
        key = head.lower()
        if key in seen:
            continue
        seen.add(key)
        status = str(d.get("status") or "").strip().lower() or "tidak diketahui"
        judul = str(d.get("judul") or "").strip()
        reference = str(d.get("reference") or "").strip()
        hierarchy = str(d.get("hierarchy") or "").strip()
        source_url = str(d.get("source_url") or "").strip()
        marker = (" (status: " + status + ")") if status in ("diubah", "dicabut") else ""
        piece = "Peraturan" + marker + ": " + head
        if judul and judul.lower() not in head.lower():
            piece = piece + "\nTentang: " + _clip(judul, 200)
        piece = piece + "\nIsi: " + _clip(isi, 1300)
        blocks.append(piece)
        sources.append({"sumber": "Peraturan", "judul": head,
                        "ref": (reference or hierarchy), "url": source_url})
        if status in ("diubah", "dicabut"):
            pasal_txt = str(cit.get("pasal") or d.get("pasal") or "").strip()
            if sp_regs:
                catatan.append(
                    "CATATAN STATUS HUKUM: " + head + " berstatus " + status
                    + ". Isi di atas adalah bunyi ASLI pasal tersebut dan BOLEH "
                    "dikutip sebagai isi pasal yang diminta pengguna. Peraturan yang "
                    "MENGUBAH pasal ini (verifikasi bunyi perubahannya sebelum "
                    "dijadikan dasar): " + "; ".join(sp_regs)
                    + ". Sebutkan status '" + status + "' ini pada bagian DASAR HUKUM.")
            elif um_regs:
                catatan.append(
                    "CATATAN STATUS HUKUM: " + head + " berstatus " + status
                    + ". Isi di atas adalah bunyi ASLI pasal tersebut dan BOLEH "
                    "dikutip sebagai isi pasal yang diminta pengguna. Peraturan "
                    "INDUK-nya telah beberapa kali diubah oleh: " + "; ".join(um_regs)
                    + ". JANGAN mengklaim salah satu pasti mengubah Pasal "
                    + (pasal_txt or "tersebut") + " tanpa verifikasi; periksa "
                    "perubahan spesifik pasal ini di peraturan pengubah. Sebutkan "
                    "status '" + status + "' ini pada bagian DASAR HUKUM.")
            else:
                catatan.append(
                    "CATATAN STATUS HUKUM: " + head + " berstatus " + status
                    + ". Isi di atas adalah bunyi ASLI pasal tersebut (boleh dikutip "
                    "sebagai isi pasal yang diminta); peraturan pengubah spesifik "
                    "belum tercatat di database — JANGAN mengarang pengganti, "
                    "sarankan konfirmasi ke TL/SC.")

    if not blocks:
        return _prev(q, limit)

    cit_text = "\n\n".join(list(catatan) + blocks)
    cit_sources = list(sources)

    # Pelengkap: konteks 'berlaku' normal (tanpa catatan kontradiktif dari
    # successor_patch). Gerbang boleh mengosongkannya -> tetap aman.
    try:
        extra = _pdb.search(q, limit, ("berlaku",)) if _pdb is not None else []
    except Exception:
        extra = []
    for d in _rows_to_dicts(extra):
        isi = str(d.get("isi") or "").strip()
        if not isi:
            continue
        head = _head_of(d)
        key = head.lower()
        if key in seen:
            continue
        seen.add(key)
        judul = str(d.get("judul") or "").strip()
        piece = "Peraturan: " + head
        if judul and judul.lower() not in head.lower():
            piece = piece + "\nTentang: " + _clip(judul, 200)
        piece = piece + "\nIsi: " + _clip(isi, 700)
        cit_text = cit_text + "\n\n" + piece
        cit_sources.append({"sumber": "Peraturan", "judul": head,
                            "ref": str(d.get("reference") or d.get("hierarchy") or ""),
                            "url": str(d.get("source_url") or "")})

    return (cit_text, cit_sources)


def _install():
    global _prev
    if _prev is not None:
        return
    prev = getattr(_re, "_ctx_peraturan", None)
    if prev is None:
        print("[rag_citation_fetch] _ctx_peraturan belum ada; patch tidak dipasang.",
              flush=True)
        return
    _prev = prev
    _re._ctx_peraturan = _ctx_peraturan_citation
    try:
        _re._DISPATCH["peraturan"] = _ctx_peraturan_citation
    except Exception:
        pass
    print("[rag_citation_fetch] fetch sitasi nomor+pasal aktif "
          "(tarik ISI lintas status, kebal gate; gate per-profil via knob_store).",
          flush=True)


if _env_on():
    _install()
else:
    print("[rag_citation_fetch] dimatikan (RAG_CITATION_FETCH=0).", flush=True)


def _selftest(q):
    global _prev
    if _prev is None:
        _prev = getattr(_re, "_ctx_peraturan", None)
    cit = _parse_citation(q)
    print("parse:", cit)
    if not cit:
        print("(bukan query sitasi nomor+pasal -> passthrough)")
        return
    units = _fetch_units(cit["jenis"], cit["nomor"], cit["tahun"], cit["pasal"])
    print("units ditemukan:", len(units))
    for d in units:
        print("-", d.get("jenis_peraturan"), d.get("nomor"), "pasal",
              d.get("pasal"), "status", d.get("status"), "sid", d.get("source_id"))
    sp, um = _amender_pointer(cit.get("nomor"), cit.get("tahun"), cit.get("pasal"))
    print("pengubah spesifik-pasal:", sp)
    print("pengubah induk (umum):", um)
    txt, src = _ctx_peraturan_citation(q, 4)
    print("\n=== KONTEKS ===\n" + (txt or "(kosong)"))
    print("\n=== SUMBER (%d) ===" % len(src))
    for s in src:
        print("-", s)


if __name__ == "__main__":
    import sys
    _q = " ".join(sys.argv[1:]) or "isi Pasal 40 PMK 81 Tahun 2024"
    _selftest(_q)
