# -*- coding: utf-8 -*-
"""
rag/ingest_dedup_patch.py
-------------------------
Tahap 2 - GUARD DEDUP INGEST (env-gated, default OFF).

Mencegah duplikat hasil scraping ganda (mis. PMK-39/PMK.03/2010 yang sempat
tersimpan 2 kali: satu 'berlaku', satu 'dicabut', beda source_id, isi identik)
agar tidak muncul lagi saat re-scrape / impor ulang.

Cara kerja (membungkus peraturan.db.upsert_peraturan sebagai lapis TERLUAR):
  * Untuk tiap UNIT yang akan di-upsert, hitung kunci konten:
    (jenis_peraturan, nomor, tahun, hash-isi ternormalisasi).
  * Cari unit lain di peraturan_unit dengan kunci SAMA tetapi source_id BEDA
    dan id BEDA -> itu salinan dari scraping terpisah (duplikat sejati).
  * Bila ketemu: JANGAN buat baris baru. Rekonsiliasi status pada baris yang
    SUDAH ada ke status yang LEBIH KETAT (dicabut > diubah > berlaku), sehingga
    salinan 'berlaku' basi tak bisa bocor lagi. Deterministik: hasil sama tak
    peduli urutan impor salinan mana yang lebih dulu.
  * Query tanpa nomor / isi terlalu pendek (lampiran/dokumen mandiri) ->
    passthrough (tak ada dedup; nol risiko salah-gabung).
  * Fail-open: error apa pun -> jatuh ke upsert asli agar ingest tak pernah patah.

Knob: RAG_INGEST_DEDUP (default OFF; set 1/true/yes/on untuk aktif).
Tak memengaruhi jalur retrieval/eval (upsert_peraturan hanya dipakai saat ingest).
"""
import os
import re
import hashlib

import peraturan.db as _pdb

_ENV = "RAG_INGEST_DEDUP"
_MIN_ISI = 40
_PRIOR = {"berlaku": 0, "diubah": 1, "dicabut": 2}


def _on():
    return str(os.environ.get(_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def _log(msg):
    try:
        print("[rag_ingest_dedup] " + msg, flush=True)
    except Exception:
        pass


def _norm_isi(t):
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _hash(t):
    return hashlib.sha1(_norm_isi(t).encode("utf-8", "replace")).hexdigest()


def _to_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _stricter(a, b):
    a = a or "berlaku"
    b = b or "berlaku"
    return a if _PRIOR.get(a, 0) >= _PRIOR.get(b, 0) else b


_orig_upsert = _pdb.upsert_peraturan


def _find_dup(c, data):
    """Cari salinan duplikat (kunci konten sama, source_id beda). None bila tak ada."""
    jenis = (data.get("jenis_peraturan") or "").strip()
    nomor = (data.get("nomor") or "").strip()
    isi = data.get("isi") or ""
    sid = (data.get("source_id") or "").strip()
    rid = data.get("id")
    if not nomor or not isi:
        return None
    if len(re.sub(r"\s+", "", isi)) < _MIN_ISI:
        return None
    tahun = _to_int(data.get("tahun"))
    target_h = _hash(isi)
    try:
        rows = c.execute(
            "SELECT id, source_id, status, tahun, isi FROM peraturan_unit "
            "WHERE jenis_peraturan = ? AND nomor = ?",
            (jenis, nomor),
        ).fetchall()
    except Exception:
        return None
    for r in rows:
        if r["id"] == rid:
            continue
        if (r["source_id"] or "").strip() == sid:
            continue
        if _to_int(r["tahun"]) != tahun:
            continue
        if _hash(r["isi"]) != target_h:
            continue
        return r
    return None


def _reconcile_status(c, dup, data):
    """Naikkan status baris yang dipertahankan ke yang lebih ketat bila perlu."""
    ex_status = dup["status"] or "berlaku"
    inc_status = data.get("status") or "berlaku"
    winner = _stricter(inc_status, ex_status)
    if winner == ex_status:
        return False
    cols = ["status = ?"]
    args = [winner]
    for k in ("dicabut_oleh", "diubah_oleh", "valid_to"):
        v = data.get(k)
        if v is not None and str(v).strip() != "":
            cols.append("%s = ?" % k)
            args.append(v)
    args.append(dup["id"])
    try:
        c.execute(
            "UPDATE peraturan_unit SET %s, updated_at = datetime('now') WHERE id = ?"
            % ", ".join(cols),
            tuple(args),
        )
        return True
    except Exception:
        return False


def _upsert_dedup(data, conn=None):
    if not _on():
        return _orig_upsert(data, conn=conn)
    own = conn is None
    c = conn or _pdb.init_db(_pdb.connect())
    try:
        try:
            dup = _find_dup(c, data)
        except Exception:
            dup = None
        if dup is not None:
            changed = _reconcile_status(c, dup, data)
            try:
                c.commit()
            except Exception:
                pass
            _log(
                "lewati duplikat: %s %s (th %s) id_baru=%s -> id_ada=%s%s"
                % (
                    data.get("jenis_peraturan") or "",
                    data.get("nomor") or "",
                    data.get("tahun"),
                    data.get("id"),
                    dup["id"],
                    ("; status->%s" % (data.get("status") or "berlaku")) if changed else "",
                )
            )
            return {"id": dup["id"], "vec_ok": False, "dedup": True}
        return _orig_upsert(data, conn=c)
    except Exception:
        # fail-open: guard tak boleh membuat ingest patah
        try:
            return _orig_upsert(data, conn=c)
        except Exception:
            raise
    finally:
        if own:
            try:
                c.close()
            except Exception:
                pass


def _install():
    if getattr(_pdb.upsert_peraturan, "_dedup_wrapped", False):
        return
    _upsert_dedup._dedup_wrapped = True
    _pdb.upsert_peraturan = _upsert_dedup


if _on():
    _install()
    _log(
        "aktif (dedup ingest: nomor+tahun+hash-isi; salinan beda source_id digabung, "
        "status dipilih paling ketat)."
    )
else:
    _log("nonaktif (set RAG_INGEST_DEDUP=1 untuk mengaktifkan).")
