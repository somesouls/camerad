# -*- coding: utf-8 -*-
"""rag_xref_patch.py — Ekspansi 1-hop "pasal terkait" (cross-reference).

Bahasa peraturan saling merujuk: "sebagaimana dimaksud dalam Pasal X",
"kecuali Pasal Y", "dengan tetap memperhatikan Pasal Z". Mesin retrieval lama
menyimpan tiap pasal sebagai unit terpisah TANPA graf sitasi, sehingga pasal
yang DIRUJUK oleh hasil teratas tidak ikut tertarik ke konteks. Untuk asisten
"Agent Kring Pajak" yang harus menemukan peraturan TERKAIT, ini celah penting.

Patch ini membungkus rag_engine._ctx_peraturan (versi successor-tracing) agar:
  1. Tetap memakai konteks BERLAKU + successor-tracing (dicabut/diubah/pengganti)
     apa adanya -- tidak mengubah perilaku lama.
  2. Untuk hasil teratas, mengekstrak rujukan "Pasal N" DI DALAM peraturan yang
     SAMA (mengurai isi pasal), lalu menarik ISI pasal yang dirujuk (yang masih
     berlaku, maksimum RAG_XREF_MAX) sebagai blok "pasal terkait" + menambah
     sumbernya. Hanya 1-hop, dedup, dan gagal-anggun.

Melengkapi rag_successor_patch: successor menelusuri relasi ANTAR-peraturan
(status_terkait/dicabut_oleh/diubah_oleh), sedangkan patch ini menelusuri
rujukan pasal DI DALAM dokumen (intra-peraturan), kasus paling sering pada frasa
"sebagaimana dimaksud dalam Pasal ...".

Env:
  RAG_XREF      '1' (default) aktif; '0'/'false'/'no'/'off' -> matikan.
  RAG_XREF_MAX  jumlah maksimum pasal terkait yang ditarik (default 3).

Dipasang via import (dari rag_rerank_patch, sesudah rag_successor_patch) agar
membungkus versi _ctx_peraturan terakhir. Karena rag_engine memakai tabel
dispatch _DISPATCH yang menyimpan referensi fungsi, patch WAJIB memperbarui
_DISPATCH["peraturan"] juga, bukan hanya atribut modul.

Gagal-anggun: bila penelusuran error, kembalikan konteks dasar apa adanya.
"""
import os
import re

import rag_engine as _re

_orig_ctx = _re._ctx_peraturan

# "Pasal 4", "Pasal 4A", "Pasal 44 ayat (2)" -> tangkap nomor pasalnya saja.
_RE_PASAL = re.compile(r"[Pp]asal\s+(\d+[A-Za-z]?)")


def _enabled():
    return str(os.environ.get("RAG_XREF", "1")).strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def _max_xref():
    try:
        return max(0, int(os.environ.get("RAG_XREF_MAX", "3")))
    except Exception:
        return 3


def _clip(s, n):
    try:
        return _re._clip(s, n)
    except Exception:
        s = s or ""
        return s if len(s) <= n else (s[:n] + "…")


def _as_dict(r):
    try:
        return r if isinstance(r, dict) else dict(r)
    except Exception:
        return None


def _ctx_peraturan_xref(q, limit=4):
    # Konteks dasar (BERLAKU + successor-tracing) di luar try: kegagalan di sini
    # harus berperilaku persis seperti sebelum patch (tidak ditelan).
    base_text, base_sources = _orig_ctx(q, limit)
    try:
        if not _enabled():
            return base_text, base_sources
        maks = _max_xref()
        if maks <= 0:
            return base_text, base_sources
        pdb = getattr(_re, "pdb", None)
        if pdb is None:
            return base_text, base_sources

        seed = pdb.search(q, 2, ("berlaku",)) or []
        if not seed:
            return base_text, base_sources

        # Pasal yang JADI seed tak perlu ditarik ulang (sudah di konteks dasar).
        pasal_diri = set()
        for r in seed:
            d = _as_dict(r)
            if not d:
                continue
            p = str(d.get("pasal") or "").strip().lower()
            if p:
                pasal_diri.add(p)

        blocks, sources = [], []
        seen_key = set()
        ditarik = 0

        for r in seed:
            if ditarik >= maks:
                break
            d = _as_dict(r)
            if not d:
                continue
            nomor = str(d.get("nomor") or "").strip()
            jenis = str(d.get("jenis_peraturan") or "").strip()
            isi = str(d.get("isi") or "")
            p_diri = str(d.get("pasal") or "").strip().lower()
            if not nomor or not isi:
                continue

            # Rujukan "Pasal N" di dalam isi (buang rujukan ke pasal diri sendiri
            # atau yang sudah jadi seed), urut kemunculan, unik.
            refs = []
            for m in _RE_PASAL.finditer(isi):
                pn = m.group(1)
                pl = pn.lower()
                if pl == p_diri or pl in pasal_diri:
                    continue
                if pl not in [x.lower() for x in refs]:
                    refs.append(pn)
            if not refs:
                continue

            # Ambil unit-unit peraturan yang SAMA, indeks per pasal.
            try:
                units = pdb.peraturan_tersusun(nomor, jenis or None) or []
            except Exception:
                units = []
            by_pasal = {}
            for u in units:
                ud = _as_dict(u)
                if not ud:
                    continue
                up = str(ud.get("pasal") or "").strip().lower()
                if up and up not in by_pasal:
                    by_pasal[up] = ud

            for pn in refs:
                if ditarik >= maks:
                    break
                u = by_pasal.get(pn.lower())
                if not u:
                    continue
                if str(u.get("status") or "berlaku").strip().lower() != "berlaku":
                    continue
                u_isi = str(u.get("isi") or "").strip()
                if not u_isi:
                    continue
                tahun = str(u.get("tahun") or "").strip()
                tajuk = " ".join(x for x in [
                    jenis, nomor, ("Tahun " + tahun) if tahun else "",
                ] if x).strip()
                head = tajuk or str(u.get("judul") or "") or "Peraturan"
                head = head + " - Pasal " + pn
                key = head.lower()
                if key in seen_key:
                    continue
                seen_key.add(key)
                pasal_diri.add(pn.lower())
                piece = ("Peraturan (pasal terkait, dirujuk): " + head +
                         "\nIsi: " + _clip(u_isi, 700))
                blocks.append(piece)
                sources.append({
                    "sumber": "Peraturan",
                    "judul": head,
                    "ref": str(u.get("reference") or u.get("hierarchy") or ""),
                    "url": str(u.get("source_url") or ""),
                })
                ditarik += 1

        if not blocks:
            return base_text, base_sources
        extra = "\n\n".join(blocks)
        new_text = (base_text + "\n\n" + extra) if base_text else extra
        return new_text, (list(base_sources or []) + sources)
    except Exception:
        return base_text, base_sources


# Pasang: ganti fungsi modul DAN entri tabel dispatch (yang menyimpan referensi
# fungsi sejak impor rag_engine / patch sebelumnya).
_re._ctx_peraturan = _ctx_peraturan_xref
try:
    _re._DISPATCH["peraturan"] = _ctx_peraturan_xref
except Exception:
    pass

try:
    print("[rag_xref_patch] ekspansi pasal terkait 1-hop aktif (maks=%d)" % _max_xref())
except Exception:
    pass
