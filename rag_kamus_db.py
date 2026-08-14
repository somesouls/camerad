# -*- coding: utf-8 -*-
"""rag_kamus_db.py — Kamus sinonim/istilah pajak untuk query rewriting (Tahap 5).

Tujuan: menjembatani vocabulary mismatch antara bahasa awam pengguna dan bahasa
hukum/formal pada korpus PERATURAN. Menyimpan pemetaan istilah baku (formal) ke
daftar sinonim/variasi awam, lalu dipakai rag_rewrite.expand_kamus() untuk
memperluas query sebelum retrieval hybrid (FTS5 + e5).

Tabel `kamus_sinonim`:
  id         INTEGER PK
  istilah    TEXT  -- bentuk baku/formal (mis. "Pajak Pertambahan Nilai")
  sinonim    TEXT  -- JSON array variasi awam (mis. ["ppn","pajak jualan"])
  kategori   TEXT  -- pengelompokan bebas (mis. "PPN", "akronim")
  catatan    TEXT
  aktif      INTEGER DEFAULT 1
  created_at, updated_at TEXT

Pola koneksi mengikuti modul *_db.py camerad lain (sqlite3 + WAL). Gagal-anggun:
semua fungsi baca mengembalikan nilai kosong bila DB/tabel bermasalah.
"""
import os
import re
import json
import sqlite3

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_BUSY_TIMEOUT_MS = 15000


def default_db_path():
    return os.environ.get("PIPELINE_KAMUS_DB_FILE") or os.path.join(_BASE_DIR, "rag_kamus.db")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_TIMEOUT_MS)
    return conn


_INIT_DONE = set()


def init_db(conn, force=False):
    key = None
    try:
        r = conn.execute("PRAGMA database_list").fetchone()
        key = r[2] if r else None
    except Exception:
        key = None
    if not force and key and key in _INIT_DONE:
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kamus_sinonim (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            istilah    TEXT NOT NULL,
            sinonim    TEXT,
            kategori   TEXT,
            catatan    TEXT,
            aktif      INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_kamus_aktif ON kamus_sinonim(aktif);
        """
    )
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    try:
        seed_default(conn)
    except Exception:
        pass
    return conn


# --------------------------------------------------------------- cache
_CACHE = {"sig": None, "rows": None}


def _sig(conn):
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM kamus_sinonim").fetchone()
        return (int(r[0]), str(r[1]))
    except Exception:
        return (0, "")


def _cache_clear():
    _CACHE["sig"] = None
    _CACHE["rows"] = None


def _json_list(v):
    try:
        x = json.loads(v) if v else []
        return [str(t).strip() for t in x if str(t).strip()] if isinstance(x, list) else []
    except Exception:
        return []


# --------------------------------------------------------------- tulis
def _norm_sinonim(v):
    if isinstance(v, list):
        arr = [str(t).strip() for t in v if str(t).strip()]
    else:
        arr = [t.strip() for t in re.split(r"[,\n;]+", str(v or "")) if t.strip()]
    out = []
    for t in arr:
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def upsert(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        istilah = str(data.get("istilah") or "").strip()
        if not istilah:
            raise ValueError("field 'istilah' wajib diisi")
        sinonim = json.dumps(_norm_sinonim(data.get("sinonim")), ensure_ascii=False)
        kategori = str(data.get("kategori") or "").strip() or None
        catatan = str(data.get("catatan") or "").strip() or None
        aktif = 0 if str(data.get("aktif")) in ("0", "false", "False", "no") else 1
        idv = data.get("id")
        if idv:
            conn.execute(
                "UPDATE kamus_sinonim SET istilah=?, sinonim=?, kategori=?, catatan=?, "
                "aktif=?, updated_at=datetime('now') WHERE id=?",
                (istilah, sinonim, kategori, catatan, aktif, int(idv)),
            )
            new_id = int(idv)
        else:
            cur = conn.execute(
                "INSERT INTO kamus_sinonim(istilah, sinonim, kategori, catatan, aktif) "
                "VALUES (?,?,?,?,?)",
                (istilah, sinonim, kategori, catatan, aktif),
            )
            new_id = int(cur.lastrowid)
        conn.commit()
        _cache_clear()
        return {"id": new_id}
    finally:
        if own:
            conn.close()


def delete(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM kamus_sinonim WHERE id=?", (int(id_),))
        conn.commit()
        _cache_clear()
        return {"dihapus": 1}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- baca
def get(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT * FROM kamus_sinonim WHERE id=?", (int(id_),)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def list_all(q="", limit=500, offset=0, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (q or "").strip()
        if q:
            rows = conn.execute(
                "SELECT * FROM kamus_sinonim WHERE istilah LIKE ? OR sinonim LIKE ? "
                "OR COALESCE(kategori,'') LIKE ? ORDER BY istilah LIMIT ? OFFSET ?",
                ("%" + q + "%", "%" + q + "%", "%" + q + "%", limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kamus_sinonim ORDER BY istilah LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["sinonim_list"] = _json_list(d.get("sinonim"))
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def all_active(conn=None):
    """List entri aktif (dengan cache) sebagai [{istilah, forms:[...]}]."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        sig = _sig(conn)
        if _CACHE["sig"] == sig and _CACHE["rows"] is not None:
            return _CACHE["rows"]
        try:
            rows = conn.execute(
                "SELECT istilah, sinonim FROM kamus_sinonim WHERE COALESCE(aktif,1)=1"
            ).fetchall()
        except Exception:
            rows = []
        out = []
        for r in rows:
            d = dict(r)
            istilah = str(d.get("istilah") or "").strip()
            if not istilah:
                continue
            forms = [istilah] + _json_list(d.get("sinonim"))
            uniq = []
            for f in forms:
                if f and f.lower() not in [x.lower() for x in uniq]:
                    uniq.append(f)
            out.append({"istilah": istilah, "forms": uniq})
        _CACHE["sig"] = sig
        _CACHE["rows"] = out
        return out
    finally:
        if own:
            conn.close()


def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = conn.execute("SELECT COUNT(*) FROM kamus_sinonim").fetchone()[0] or 0
        na = conn.execute("SELECT COUNT(*) FROM kamus_sinonim WHERE COALESCE(aktif,1)=1").fetchone()[0] or 0
        return {"total": int(n), "aktif": int(na)}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- expand
def _has_term(text_low, term):
    t = (term or "").strip().lower()
    if not t:
        return False
    return re.search(r"(?<![0-9a-z])" + re.escape(t) + r"(?![0-9a-z])", text_low) is not None


def expand_terms(text, maks=12, conn=None):
    """Kembalikan daftar istilah tambahan untuk memperluas query.

    Untuk tiap entri: bila SALAH SATU bentuk (istilah/sinonim) muncul di teks,
    tambahkan bentuk lain yang BELUM ada di teks. Dipakai rag_rewrite.
    """
    text_low = (text or "").lower()
    if not text_low.strip():
        return []
    try:
        entries = all_active(conn=conn)
    except Exception:
        entries = []
    extra = []
    for e in entries:
        forms = e.get("forms") or []
        if not any(_has_term(text_low, f) for f in forms):
            continue
        for f in forms:
            if not _has_term(text_low, f) and f.lower() not in [x.lower() for x in extra]:
                extra.append(f)
        if len(extra) >= maks:
            break
    return extra[:maks]


# --------------------------------------------------------------- seed
_DEFAULT_SEED = [
    {"istilah": "Pajak Pertambahan Nilai", "sinonim": ["PPN", "pajak jualan", "pajak pertambahan"], "kategori": "akronim"},
    {"istilah": "Pajak Penghasilan", "sinonim": ["PPh", "pajak gaji", "pajak penghasilan"], "kategori": "akronim"},
    {"istilah": "Pengusaha Kena Pajak", "sinonim": ["PKP"], "kategori": "akronim"},
    {"istilah": "Nomor Pokok Wajib Pajak", "sinonim": ["NPWP"], "kategori": "akronim"},
    {"istilah": "Wajib Pajak", "sinonim": ["WP"], "kategori": "akronim"},
    {"istilah": "Surat Pemberitahuan", "sinonim": ["SPT", "lapor pajak", "laporan pajak"], "kategori": "akronim"},
    {"istilah": "Surat Ketetapan Pajak", "sinonim": ["SKP"], "kategori": "akronim"},
    {"istilah": "restitusi", "sinonim": ["pengembalian pajak", "minta balik pajak", "refund pajak"], "kategori": "istilah"},
    {"istilah": "jasa angkutan laut", "sinonim": ["jasa pelayaran", "jasa kapal", "pelayaran"], "kategori": "objek"},
    {"istilah": "Bea Perolehan Hak atas Tanah dan Bangunan", "sinonim": ["BPHTB"], "kategori": "akronim"},
    {"istilah": "Pajak Bumi dan Bangunan", "sinonim": ["PBB"], "kategori": "akronim"},
    {"istilah": "faktur pajak", "sinonim": ["invoice pajak", "nota pajak"], "kategori": "istilah"},
]


def seed_default(conn):
    """Isi beberapa entri umum bila tabel masih kosong (idempoten)."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM kamus_sinonim").fetchone()[0] or 0
    except Exception:
        return
    if n:
        return
    for row in _DEFAULT_SEED:
        try:
            upsert(row, conn=conn)
        except Exception:
            pass
