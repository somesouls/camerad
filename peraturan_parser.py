# -*- coding: utf-8 -*-
"""Parser HTML halaman peraturan TKB DJP -> unit terstruktur untuk RAG.

Port dari jakai (app/parsers/tkb_djp.py). Menelusuri sel <td> pada .isi-aturan,
melacak konteks BAB/Bagian/Pasal, dan memproduksi satu unit per-Pasal (default)
atau per-ayat. Keluaran cocok untuk kolom tabel peraturan_unit (peraturan_db).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


BULAN = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}

RE_BAB = re.compile(r"^BAB\s+([IVXLCDM]+)$", re.I)
RE_BAGIAN = re.compile(r"^Bagian\s+(Kesatu|Kedua|Ketiga|Keempat|Kelima|Keenam|Ketujuh|Kedelapan|Kesembilan|Kesepuluh)$", re.I)
RE_PARAGRAF = re.compile(r"^Paragraf\s+\d+$", re.I)
RE_PASAL = re.compile(r"^Pasal\s+(\d+[A-Z]?|[IVXLCDM]+)$", re.I)
RE_AYAT = re.compile(r"^\((\d+[a-z]?)\)$")
RE_HURUF = re.compile(r"^([a-z]{1,2})\.$")
RE_ANGKA = re.compile(r"^(\d+)\.$")
RE_SUB = re.compile(r"^([a-z]{1,2})\)$")

_KEKUATAN = {
    "UU": 100, "PERPU": 95, "PP": 90, "PERPRES": 85, "PMK": 70,
    "KMK": 65, "PER": 60, "KEP": 55, "SE": 40,
}


def _clean(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _is_marker(cell: str) -> bool:
    return bool(
        RE_AYAT.match(cell) or RE_HURUF.match(cell)
        or RE_ANGKA.match(cell) or RE_SUB.match(cell)
    )


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return re.sub(r"-{2,}", "-", text)


@dataclass
class Meta:
    no_aturan: str = ""
    jenis_peraturan: str = ""
    nomor: str = ""
    tahun: Optional[int] = None
    judul: str = ""
    tanggal_teks: str = ""
    valid_from: str = ""
    source_url: str = ""
    base_id: str = ""


def _parse_meta(soup, jenis_hint: Optional[str] = None) -> Meta:
    m = Meta()

    def sel(cls: str) -> str:
        el = soup.select_one(f".{cls}")
        return _clean(el.get_text(" ")) if el else ""

    m.no_aturan = sel("no-aturan")
    m.judul = sel("perihal-aturan")
    m.tanggal_teks = sel("tanggal-aturan")

    low = m.no_aturan.lower()
    if "dirjen" in low or "direktur jenderal" in low:
        m.jenis_peraturan = "PER"
    elif "menteri keuangan" in low:
        m.jenis_peraturan = "PMK"
    elif "pemerintah" in low:
        m.jenis_peraturan = "PP"
    elif "presiden" in low:
        m.jenis_peraturan = "PERPRES"
    elif "undang" in low:
        m.jenis_peraturan = "UU"
    elif "surat edaran" in low:
        m.jenis_peraturan = "SE"
    elif "keputusan" in low:
        m.jenis_peraturan = "KEP"
    else:
        m.jenis_peraturan = "PER"

    if jenis_hint:
        m.jenis_peraturan = jenis_hint

    mnom = re.search(r"nomor\s+(.+)$", m.no_aturan, re.I)
    m.nomor = _clean(mnom.group(1)) if mnom else m.no_aturan

    mth = re.search(r"(19|20)\d{2}", m.nomor) or re.search(r"(19|20)\d{2}", m.tanggal_teks)
    if mth:
        m.tahun = int(mth.group(0))

    md = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+((?:19|20)\d{2})", m.tanggal_teks)
    if md:
        day = int(md.group(1))
        mon = BULAN.get(md.group(2).lower())
        yr = int(md.group(3))
        if mon:
            m.valid_from = f"{yr:04d}-{mon:02d}-{day:02d}"

    pdf = soup.find("a", href=re.compile(r"\.pdf", re.I))
    if pdf and pdf.get("href"):
        href = pdf["href"].strip()
        mmd = re.search(r"\((https?://[^)]+)\)", href)
        m.source_url = mmd.group(1) if mmd else href

    m.base_id = slugify(m.nomor) or slugify(m.no_aturan) or "peraturan"
    return m


def _collect_cells(soup) -> list:
    isi = soup.select_one(".isi-aturan") or soup
    cells = []
    for td in isi.find_all("td"):
        if td.find("table") is not None:
            continue
        cells.append(_clean(td.get_text(" ")))
    return cells


@dataclass
class Unit:
    pasal: str = ""
    ayat: str = ""
    bab: str = ""
    bagian: str = ""
    judul_bagian: str = ""
    hierarchy: str = ""
    isi: str = ""


def _walk(cells: list, per_ayat: bool = False) -> list:
    units = []
    bab = bab_judul = bagian = bagian_judul = ""
    mode_title = None  # 'bab' | 'bagian'
    pending = ""
    cur = None
    cur_ayat = None

    def bab_full() -> str:
        return _clean(f"{bab} {bab_judul}")

    def bagian_full() -> str:
        return _clean(f"{bagian} {bagian_judul}")

    def hierarchy_for(pasal: str, ayat: str = "") -> str:
        parts = [p for p in [bab_full(), bagian_full(), f"Pasal {pasal}" if pasal else ""] if p]
        h = " > ".join(parts)
        if ayat:
            h += f" ayat ({ayat})"
        return h

    def flush():
        nonlocal cur, cur_ayat
        if per_ayat:
            if cur_ayat and cur_ayat.isi.strip():
                units.append(cur_ayat)
            cur_ayat = None
        if cur and cur.isi.strip() and not per_ayat:
            units.append(cur)
        cur = None

    for raw in cells:
        c = raw.strip()
        if not c:
            continue

        if RE_BAB.match(c):
            flush()
            bab, bab_judul, bagian, bagian_judul = c.upper(), "", "", ""
            mode_title = "bab"
            continue
        if RE_BAGIAN.match(c):
            flush()
            bagian, bagian_judul = c, ""
            mode_title = "bagian"
            continue
        if RE_PARAGRAF.match(c):
            mode_title = "bagian"
            bagian_judul = _clean(bagian_judul)
            continue

        mp = RE_PASAL.match(c)
        if mp:
            flush()
            pasal_no = mp.group(1)
            cur = Unit(
                pasal=pasal_no, bab=bab_full(), bagian=bagian, judul_bagian=bagian_judul,
                hierarchy=hierarchy_for(pasal_no),
            )
            cur_ayat = None
            mode_title = None
            pending = ""
            continue

        if mode_title and not _is_marker(c):
            if len(c) <= 90 and not c.endswith("."):
                if mode_title == "bab":
                    bab_judul = _clean(f"{bab_judul} {c}")
                else:
                    bagian_judul = _clean(f"{bagian_judul} {c}")
                continue
            mode_title = None

        if cur is None:
            continue

        ma = RE_AYAT.match(c)
        if ma and per_ayat:
            if cur_ayat and cur_ayat.isi.strip():
                units.append(cur_ayat)
            cur_ayat = Unit(
                pasal=cur.pasal, ayat=ma.group(1), bab=cur.bab, bagian=cur.bagian,
                judul_bagian=cur.judul_bagian, hierarchy=hierarchy_for(cur.pasal, ma.group(1)),
            )
            pending = ""
            continue

        if _is_marker(c):
            pending = _clean(f"{pending} {c}") if pending else c
            continue

        seg = _clean(f"{pending} {c}") if pending else c
        pending = ""
        target = cur_ayat if (per_ayat and cur_ayat is not None) else cur
        if per_ayat and cur_ayat is None:
            cur_ayat = Unit(
                pasal=cur.pasal, ayat="", bab=cur.bab, bagian=cur.bagian,
                judul_bagian=cur.judul_bagian, hierarchy=hierarchy_for(cur.pasal),
            )
            target = cur_ayat
        target.isi = _clean(f"{target.isi} {seg}")

    flush()
    return units


def is_regulation_html(html: str) -> bool:
    """True bila HTML tampak halaman peraturan TKB (punya isi-aturan/#peraturan)."""
    if BeautifulSoup is None:
        return bool(re.search(r"Pasal\s+\d+", html or ""))
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one(".isi-aturan") or soup.select_one("#peraturan"):
        return True
    for td in soup.find_all("td"):
        if RE_PASAL.match(_clean(td.get_text(" "))):
            return True
    return False


def parse(html: str, per_ayat: bool = False, jenis_hint: Optional[str] = None):
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4/lxml belum terpasang (pip install beautifulsoup4 lxml)")
    soup = BeautifulSoup(html, "lxml")
    meta = _parse_meta(soup, jenis_hint=jenis_hint)
    units = _walk(_collect_cells(soup), per_ayat=per_ayat)
    return meta, units


def to_rows(html: str, per_ayat: bool = False, jenis_hint: Optional[str] = None):
    meta, units = parse(html, per_ayat=per_ayat, jenis_hint=jenis_hint)
    kekuatan = _KEKUATAN.get(meta.jenis_peraturan, 50)
    rows = []
    for u in units:
        suffix = f"p{u.pasal.lower()}" if u.pasal else "utuh"
        if per_ayat and u.ayat:
            suffix += f"a{u.ayat.lower()}"
        rows.append({
            "id": f"{meta.base_id}-{suffix}",
            "jenis_peraturan": meta.jenis_peraturan,
            "nomor": meta.nomor,
            "tahun": meta.tahun,
            "judul": meta.judul,
            "bab": u.bab,
            "bagian": _clean(f"{u.bagian} {u.judul_bagian}"),
            "pasal": u.pasal,
            "ayat": u.ayat,
            "hierarchy": u.hierarchy,
            "isi": u.isi,
            "status": "berlaku",
            "valid_from": meta.valid_from,
            "kekuatan_hukum": kekuatan,
            "can_cite": 1,
            "source_url": meta.source_url,
        })

    if not rows:
        cells = _collect_cells(BeautifulSoup(html, "lxml"))
        body = _clean(" ".join(c for c in cells if c))
        if len(body) >= 200:
            rows.append({
                "id": f"{meta.base_id}-utuh",
                "jenis_peraturan": meta.jenis_peraturan,
                "nomor": meta.nomor,
                "tahun": meta.tahun,
                "judul": meta.judul,
                "bab": "",
                "bagian": "",
                "pasal": "",
                "ayat": "",
                "hierarchy": "(dokumen utuh - tanpa struktur Pasal)",
                "isi": body[:20000],
                "status": "berlaku",
                "valid_from": meta.valid_from,
                "kekuatan_hukum": kekuatan,
                "can_cite": 1,
                "source_url": meta.source_url,
            })
    return meta, rows


def to_jsonl(rows) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
