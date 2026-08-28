# -*- coding: utf-8 -*-
"""rag_nomor_pin_patch.py — prioritaskan peraturan ber-nomor eksak untuk query sitasi.

Bila query menyebut nomor peraturan spesifik (mis. "bunyi pasal 19 PER-23/PJ/2016"),
hasil retrieval seharusnya menempatkan peraturan ber-nomor SAMA di atas. Reranker
cross-encoder + domain boost (recency) kadang menaikkan peraturan lain yang teksnya
mirip (mis. PER-16/PJ/2018) di atas peraturan yang justru DISEBUT eksplisit.

Patch ini membungkus peraturan_db.search sebagai lapis TERLUAR (dimuat paling akhir
lewat rag_domain_patch, jadi sesudah rerank + domain) dan:
  1. Mendeteksi token nomor peraturan pada query ASLI.
  2. Bila ada baris hasil ber-nomor sama -> dinaikkan stabil ke atas (reorder saja).
  3. Bila TIDAK ada di hasil -> satu pencarian tertarget memakai nomor sebagai query,
     lalu baris ber-nomor sama disisipkan di atas (dedup per unit), dipotong k.

Query tanpa nomor -> passthrough (nol dampak, tanpa pencarian ekstra). Reorder hanya
di dalam hasil yang SUDAH difilter status (mis. 'berlaku'), jadi tidak membangkitkan
peraturan yang sudah dicabut. Pencarian tertarget dipanggil paling banyak sekali dan
selalu ke search bawaan (tanpa rekursi ke wrapper ini), jadi aman dari loop.

Env / knob (Tahap 4e-2):
  RAG_NOMOR_PIN kini PER-PROFIL via rag.knob_store dgn precedence
  store-profil > env > default(True). Profil aktif dibaca dari rag.calibration
  (di-set rag.engine di THREAD RETRIEVAL). Artinya tiap profil (agent/chatbot)
  bisa mengaktif/menonaktifkan pin nomor sendiri dari panel /rag-harness tanpa
  memengaruhi profil lain, dan tanpa redeploy (store dibaca live).

  Catatan pemasangan: keputusan MEMASANG wrapper saat import tetap berbasis ENV
  (RAG_NOMOR_PIN=0 -> wrapper tak dipasang, patch mati global). Selama wrapper
  terpasang (default on), gate PER-PANGGILAN yang menentukan per-profil. Bila
  profil belum ditandai / modul knob tak tersedia -> jatuh ke perilaku ENV lama.

Gagal-anggun penuh: bila apa pun error, hasil search bawaan dikembalikan.
"""
import os
import re

import peraturan.db as _pdb

try:
    import rag.knob_store as _ks
except Exception:            # pragma: no cover
    _ks = None
try:
    import rag.calibration as _cal
except Exception:            # pragma: no cover
    _cal = None

_orig_search = _pdb.search

# Pola nomor peraturan DJP/Kemenkeu yang lazim dikutip pengguna.
_RE_NOMOR = re.compile(
    r"(?:PER|SE|KEP|PENG|KMK)\s*-?\s*\d+\s*/\s*PJ(?:\.\w+)?\s*/\s*\d{4}"
    r"|\d+\s*/\s*PMK\.?\s*\d*\s*/\s*\d{4}"
    r"|(?:PMK|KMK|PP|UU|PERPU|PERPPU|PERMENKEU)\s*-?\s*\d+\s*(?:TAHUN\s*)?/?\s*\d{4}",
    re.IGNORECASE,
)


def _env_on():
    """Gate berbasis ENV: fallback tingkat kedua DAN penentu pemasangan wrapper
    saat import. Default ON (hanya RAG_NOMOR_PIN=0/false/no/off yang mematikan)."""
    return str(os.environ.get("RAG_NOMOR_PIN", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _on():
    """Gate EFEKTIF per-panggilan, PER-PROFIL. Baca knob_store.resolve(
    profil_aktif, 'RAG_NOMOR_PIN') dgn precedence store-profil > env > default.
    Profil aktif dari rag.calibration (di-set rag.engine di thread retrieval).
    Bila modul knob tak ada / profil belum ditandai -> jatuh ke _env_on().
    GAGAL-ANGGUN: error apa pun -> _env_on()."""
    if _ks is None:
        return _env_on()
    prof = None
    if _cal is not None:
        try:
            prof = _cal.get_profile()
        except Exception:
            prof = None
    try:
        v = _ks.resolve(prof, "RAG_NOMOR_PIN")
        if v is None:
            return _env_on()
        return bool(v)
    except Exception:
        return _env_on()


def _norm(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _row_nomor(r):
    try:
        return _norm(r.get("nomor"))
    except Exception:
        return ""


def _matches(r, tok):
    if not isinstance(r, dict):
        return False
    nn = _row_nomor(r)
    if len(nn) < 5:
        return False
    return nn in tok or tok in nn


def _uid(r):
    try:
        return (r.get("source_id"), r.get("pasal"), r.get("jenis_unit"))
    except Exception:
        return None


def _search_nomor_pin(query, k=10, status_list=("berlaku",), conn=None):
    if not _on():
        return _orig_search(query, k=k, status_list=status_list, conn=conn)
    rows = _orig_search(query, k=k, status_list=status_list, conn=conn)
    try:
        rows = rows or []
        m = _RE_NOMOR.search(query or "")
        if not m:
            return rows
        tok = _norm(m.group(0))
        if len(tok) < 5:
            return rows
        hit = [r for r in rows if _matches(r, tok)]
        if hit:
            rest = [r for r in rows if not _matches(r, tok)]
            return (hit + rest)[:int(k or 10)]
        # Nomor disebut tapi tak ada di hasil -> pencarian tertarget sekali.
        supp = _orig_search(
            m.group(0), k=max(int(k or 10), 20),
            status_list=status_list, conn=conn) or []
        supp_hit = [r for r in supp if _matches(r, tok)][:3]
        if not supp_hit:
            return rows
        seen, merged = set(), []
        for r in supp_hit + rows:
            u = _uid(r)
            if u is not None and u in seen:
                continue
            if u is not None:
                seen.add(u)
            merged.append(r)
        return merged[:int(k or 10)]
    except Exception:
        return rows


if _env_on():
    _pdb.search = _search_nomor_pin
    print("[rag_nomor_pin] pin nomor-eksak aktif "
          "(query menyebut nomor -> peraturan ber-nomor sama diprioritaskan; "
          "gate per-profil via knob_store).",
          flush=True)
else:
    print("[rag_nomor_pin] dimatikan (RAG_NOMOR_PIN=0).", flush=True)
