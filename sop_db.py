# -*- coding: utf-8 -*-
"""
sop_db.py
---------
Basis data SOP & Proses Bisnis untuk camerad (sumber grounding RAG tambahan,
tampil sebagai \"Sumber 5\" pada Agent Kring Pajak).

Dokumen SOP/Proses Bisnis diekstrak dari PDF/PPT/DOCX/TXT/HTML lalu DIPECAH
per-bagian (heading) dan disimpan PERMANEN sebagai unit-unit pada tabel
sop_unit -> bisa langsung dipakai mesin chat RAG. Berbeda dari menu Studio
Dokumen yang hanya memproses sesaat, hasil di sini menetap di basis data.

Keputusan teknis mengikuti peraturan_db.py:
  * TANPA sqlite-vec: vektor e5 disimpan BLOB pada sop_vec, cosine dihitung di
    Python (numpy). Embedder dipakai ULANG dari peraturan_semantic (model e5).
  * Retrieval hybrid = FTS5 (lexical) + vektor e5 (semantik), digabung RRF.
  * Gagal-anggun: tanpa FTS5 -> LIKE; tanpa embedding -> FTS/LIKE saja.
  * Koneksi WAL + busy_timeout 30 dtk untuk menekan 'database is locked'.

Model data: satu berkas = satu \"dokumen\" (dokumen_id stabil dari source_id /
nama berkas). Tiap bagian (hasil pemotongan per-heading) menjadi satu baris
sop_unit; kolom 'urutan' menjaga urutan asli bagian di dalam dokumen.
"""
import os
import re
import sqlite3

import peraturan_semantic as psem

try:
    import numpy as np
except Exception:
    np = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

RRF_K = 60
_BUSY_TIMEOUT_MS = 30000

SOP_KOLOM = [
    "id", "dokumen_id", "judul", "kategori", "bagian", "urutan",
    "isi", "ringkasan", "sumber_tipe", "status",
    "source_url", "source_file", "source_id",
]

_INT_FIELDS = ("urutan",)

IMPOR_KOLOM = [
    "file", "dokumen_id", "judul", "kategori", "tipe",
    "n_unit", "status", "catatan",
]

KATEGORI_VALID = ("SOP", "Proses Bisnis", "Panduan", "Lainnya")


def default_db_path():
    return os.environ.get("PIPELINE_SOP_DB_FILE") or os.path.join(_BASE_DIR, "sop.db")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_TIMEOUT_MS)
    return conn


_HAS_FTS = None


def _fts_available(conn):
    global _HAS_FTS
    if _HAS_FTS is not None:
        return _HAS_FTS
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _sop_fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _sop_fts_probe")
        _HAS_FTS = True
    except Exception:
        _HAS_FTS = False
    return _HAS_FTS


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
        CREATE TABLE IF NOT EXISTS sop_unit (
            id          TEXT PRIMARY KEY,
            dokumen_id  TEXT,
            judul       TEXT,
            kategori    TEXT,
            bagian      TEXT,
            urutan      INTEGER DEFAULT 0,
            isi         TEXT,
            ringkasan   TEXT,
            sumber_tipe TEXT,
            status      TEXT DEFAULT 'aktif',
            source_url  TEXT,
            source_file TEXT,
            source_id   TEXT,
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sop_dok ON sop_unit(dokumen_id);
        CREATE INDEX IF NOT EXISTS idx_sop_kat ON sop_unit(kategori);
        CREATE INDEX IF NOT EXISTS idx_sop_src ON sop_unit(source_id);

        CREATE TABLE IF NOT EXISTS sop_vec (
            id  TEXT PRIMARY KEY,
            dim INTEGER,
            emb BLOB
        );

        CREATE TABLE IF NOT EXISTS sop_impor_log (
            file       TEXT PRIMARY KEY,
            dokumen_id TEXT,
            judul      TEXT,
            kategori   TEXT,
            tipe       TEXT,
            n_unit     INTEGER DEFAULT 0,
            status     TEXT,
            catatan    TEXT,
            ts         TEXT DEFAULT (datetime('now'))
        );
        """
    )
    if _fts_available(conn):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS sop_fts "
                "USING fts5(id UNINDEXED, judul, bagian, isi, "
                "tokenize='unicode61 remove_diacritics 2')"
            )
        except Exception:
            pass
    conn.commit()
    if key:
        _INIT_DONE.add(key)
    return conn


# --------------------------------------------------------------- cache vektor
_VEC_CACHE = {"sig": None, "ids": None, "mat": None}


def _vec_cache_clear():
    _VEC_CACHE["sig"] = None
    _VEC_CACHE["ids"] = None
    _VEC_CACHE["mat"] = None


def _vec_sig(conn):
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM sop_vec").fetchone()
        return (int(r[0]), int(r[1]))
    except Exception:
        return (0, 0)


def _load_vectors(conn):
    if np is None:
        return [], None
    sig = _vec_sig(conn)
    if _VEC_CACHE["sig"] == sig and _VEC_CACHE["mat"] is not None:
        return _VEC_CACHE["ids"], _VEC_CACHE["mat"]
    try:
        rows = conn.execute("SELECT id, emb FROM sop_vec").fetchall()
    except Exception:
        return [], None
    ids, vecs = [], []
    for r in rows:
        v = psem.from_blob(r["emb"])
        if v is None:
            continue
        ids.append(r["id"])
        vecs.append(v)
    mat = np.vstack(vecs) if vecs else None
    _VEC_CACHE["sig"] = sig
    _VEC_CACHE["ids"] = ids
    _VEC_CACHE["mat"] = mat
    return ids, mat


# --------------------------------------------------------------------- upsert
def _norm(data):
    out = {}
    for k in SOP_KOLOM:
        v = data.get(k)
        if isinstance(v, str) and v.strip() == "":
            v = None
        if k in _INT_FIELDS and v is not None and v != "":
            try:
                v = int(v)
            except Exception:
                v = 0
        out[k] = v
    if not out.get("id"):
        raise ValueError("sop unit wajib punya 'id'")
    if out.get("status") is None:
        out["status"] = "aktif"
    if out.get("urutan") is None:
        out["urutan"] = 0
    return out


def _sync_fts(conn, id_, judul, bagian, isi):
    if not _fts_available(conn):
        return
    try:
        conn.execute("DELETE FROM sop_fts WHERE id = ?", (id_,))
        conn.execute(
            "INSERT INTO sop_fts(id, judul, bagian, isi) VALUES (?, ?, ?, ?)",
            (id_, judul or "", bagian or "", isi or ""),
        )
    except Exception:
        pass


def _sync_vec(conn, id_, teks):
    try:
        conn.execute("DELETE FROM sop_vec WHERE id = ?", (id_,))
    except Exception:
        return False
    if not (teks or "").strip():
        _vec_cache_clear()
        return False
    vec = psem.embed_passage(teks)
    if vec is None:
        _vec_cache_clear()
        return False
    blob = psem.to_blob(vec)
    if blob is None:
        return False
    conn.execute(
        "INSERT INTO sop_vec(id, dim, emb) VALUES (?, ?, ?)",
        (id_, int(len(vec)), blob),
    )
    _vec_cache_clear()
    return True


def upsert_sop(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        d = _norm(data)
        cols = SOP_KOLOM
        ph = ",".join("?" for _ in cols)
        updates = ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "id")
        conn.execute(
            "INSERT INTO sop_unit(%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET %s, updated_at=datetime('now')"
            % (",".join(cols), ph, updates),
            tuple(d[c] for c in cols),
        )
        _sync_fts(conn, d["id"], d.get("judul"), d.get("bagian"), d.get("isi"))
        teks = "%s %s %s" % (d.get("judul") or "", d.get("bagian") or "", d.get("isi") or "")
        vec_ok = _sync_vec(conn, d["id"], teks)
        conn.commit()
        return {"id": d["id"], "vec_ok": vec_ok}
    finally:
        if own:
            conn.close()


def delete_dokumen(dokumen_id, conn=None):
    """Hapus seluruh unit satu dokumen (beserta vektor & FTS-nya)."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM sop_unit WHERE dokumen_id = ?", (dokumen_id,)).fetchall()]
        for id_ in ids:
            conn.execute("DELETE FROM sop_unit WHERE id = ?", (id_,))
            if _fts_available(conn):
                conn.execute("DELETE FROM sop_fts WHERE id = ?", (id_,))
            conn.execute("DELETE FROM sop_vec WHERE id = ?", (id_,))
        conn.commit()
        _vec_cache_clear()
        return {"unit_dihapus": len(ids)}
    finally:
        if own:
            conn.close()


def bulk_delete_dokumen(dokumen_ids, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for did in (dokumen_ids or []):
            n += delete_dokumen(did, conn=conn).get("unit_dihapus", 0)
        return {"unit_dihapus": n}
    finally:
        if own:
            conn.close()


# ----------------------------------------------------------------------- read
def get_dokumen(dokumen_id, conn=None):
    """Semua bagian satu dokumen, terurut sesuai 'urutan'."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT * FROM sop_unit WHERE dokumen_id = ? ORDER BY urutan, id",
            (dokumen_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def list_dokumen_grouped(q="", kategori="", limit=200, offset=0, conn=None):
    """Daftar dokumen (dikelompokkan per dokumen_id) + filter + paging."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        where, args = [], []
        if q:
            where.append("(judul LIKE ? OR isi LIKE ? OR bagian LIKE ?)")
            args += ["%" + q + "%", "%" + q + "%", "%" + q + "%"]
        if kategori:
            where.append("kategori = ?")
            args.append(kategori)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        base = (
            "SELECT dokumen_id, MIN(judul) AS judul, MIN(kategori) AS kategori, "
            "MIN(sumber_tipe) AS sumber_tipe, MIN(status) AS status, "
            "COUNT(*) AS n_unit, MIN(source_file) AS source_file, "
            "MAX(source_url) AS source_url, MAX(updated_at) AS updated_at "
            "FROM sop_unit " + wsql + " GROUP BY dokumen_id"
        )
        all_rows = conn.execute(base, tuple(args)).fetchall()
        total = len(all_rows)
        items = [dict(r) for r in all_rows]
        items.sort(key=lambda r: (r.get("updated_at") or ""), reverse=True)
        items = items[offset: offset + limit]
        return {"items": items, "total": total}
    finally:
        if own:
            conn.close()


def list_sumber(conn=None):
    """Ringkasan sumber terindeks: satu baris per (source_file, source_id).

    Dipakai fitur rekonsiliasi/audit (sop_batch.audit_folder) untuk mengecek
    berkas mana di folder yang SUDAH atau BELUM ada di DB.
    """
    own = conn is None
    conn = conn or init_db(connect())
    try:
        rows = conn.execute(
            "SELECT source_file, source_id, MIN(dokumen_id) AS dokumen_id, "
            "MIN(judul) AS judul, MIN(kategori) AS kategori, "
            "MIN(sumber_tipe) AS sumber_tipe, COUNT(*) AS n_unit "
            "FROM sop_unit GROUP BY source_file, source_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------- impor
def upsert_impor_log(rows, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for r in rows:
            vals = tuple(r.get(k) for k in IMPOR_KOLOM)
            ph = ",".join("?" for _ in IMPOR_KOLOM)
            upd = ",".join("%s=excluded.%s" % (c, c) for c in IMPOR_KOLOM if c != "file")
            conn.execute(
                "INSERT INTO sop_impor_log(%s) VALUES (%s) "
                "ON CONFLICT(file) DO UPDATE SET %s, ts=datetime('now')"
                % (",".join(IMPOR_KOLOM), ph, upd),
                vals,
            )
            n += 1
        conn.commit()
        return n
    finally:
        if own:
            conn.close()


def list_impor_log(status="", limit=800, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM sop_impor_log WHERE status = ? ORDER BY kategori, file LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sop_impor_log ORDER BY kategori, file LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------- stats
def stats(conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        def _c(sql, args=()):
            return conn.execute(sql, args).fetchone()[0] or 0

        out = {
            "total_dokumen": _c("SELECT COUNT(DISTINCT dokumen_id) FROM sop_unit"),
            "total_unit": _c("SELECT COUNT(*) FROM sop_unit"),
            "total_vec": _c("SELECT COUNT(*) FROM sop_vec"),
        }
        krow = conn.execute(
            "SELECT COALESCE(kategori,'(tanpa)') AS k, COUNT(DISTINCT dokumen_id) AS n "
            "FROM sop_unit GROUP BY kategori").fetchall()
        out["kategori"] = {r["k"]: r["n"] for r in krow}
        has_log = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sop_impor_log'"
        ).fetchone()
        triase = {}
        if has_log:
            frow = conn.execute(
                "SELECT status, COUNT(*) AS n FROM sop_impor_log GROUP BY status").fetchall()
            triase = {r["status"]: r["n"] for r in frow}
        out["triase"] = triase
        return out
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ retrieval
def _fts_query(text):
    toks = re.findall(r"\w+", text or "", flags=re.UNICODE)
    if not toks:
        return None
    return " OR ".join('"%s"' % t for t in toks)


def _like_ids(conn, query, limit=50):
    toks = re.findall(r"[0-9A-Za-z]{3,}", (query or "").lower())[:8]
    if not toks:
        return []
    where = " OR ".join(["LOWER(judul) LIKE ? OR LOWER(isi) LIKE ?"] * len(toks))
    params = []
    for t in toks:
        params += ["%" + t + "%", "%" + t + "%"]
    try:
        rows = conn.execute(
            "SELECT id FROM sop_unit WHERE " + where + " LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


def _fts_ids(conn, query, limit=50):
    if not _fts_available(conn):
        return _like_ids(conn, query, limit)
    fq = _fts_query(query)
    if not fq:
        return []
    try:
        rows = conn.execute(
            "SELECT id FROM sop_fts WHERE sop_fts MATCH ? ORDER BY rank LIMIT ?",
            (fq, limit),
        ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return _like_ids(conn, query, limit)


def _vec_ids(conn, query, limit=50):
    if np is None:
        return []
    qv = psem.embed_query(query)
    if qv is None:
        return []
    ids, mat = _load_vectors(conn)
    if mat is None or not ids:
        return []
    try:
        sims = mat @ np.asarray(qv, dtype="float32")
        order = np.argsort(-sims)[:limit]
        return [ids[int(i)] for i in order]
    except Exception:
        return []


def _rrf(*ranked):
    scores = {}
    for lst in ranked:
        for rank, id_ in enumerate(lst, start=1):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def search(query, k=8, status_list=("aktif",), conn=None):
    """Hybrid FTS5 + vektor e5 (RRF). Kembalikan list dict + 'skor'."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        q = (query or "").strip()
        if not q:
            return []
        fts = _fts_ids(conn, q)
        vec = _vec_ids(conn, q)
        scores = _rrf(fts, vec)
        if not scores:
            return []
        id_ph = ",".join("?" for _ in scores)
        st = list(status_list) if status_list else []
        if st:
            st_ph = ",".join("?" for _ in st)
            rows = conn.execute(
                "SELECT * FROM sop_unit WHERE id IN (%s) AND status IN (%s)" % (id_ph, st_ph),
                (*scores.keys(), *st),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sop_unit WHERE id IN (%s)" % id_ph,
                tuple(scores.keys()),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skor"] = scores.get(d["id"], 0.0)
            out.append(d)
        out.sort(key=lambda x: -x["skor"])
        return out[:k]
    finally:
        if own:
            conn.close()


def reindex(conn=None, batch=64):
    """(Re)hitung embedding e5 untuk seluruh baris. Perlu model tersedia."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if not psem.is_available():
            return {"ok": False, "error": "Model embedding tidak tersedia.", "n": 0}
        rows = conn.execute("SELECT id, judul, bagian, isi FROM sop_unit").fetchall()
        ids = [r["id"] for r in rows]
        texts = [((r["judul"] or "") + " " + (r["bagian"] or "") + " " + (r["isi"] or "")).strip()
                 for r in rows]
        n = 0
        for i in range(0, len(ids), batch):
            chunk_ids = ids[i:i + batch]
            chunk_txt = texts[i:i + batch]
            arr = psem.embed_passages(chunk_txt)
            if arr is None:
                continue
            for j, id_ in enumerate(chunk_ids):
                v = arr[j]
                conn.execute("DELETE FROM sop_vec WHERE id=?", (id_,))
                conn.execute(
                    "INSERT INTO sop_vec(id, dim, emb) VALUES (?,?,?)",
                    (id_, int(len(v)), psem.to_blob(v)),
                )
                n += 1
            conn.commit()
        _vec_cache_clear()
        return {"ok": True, "n": n}
    finally:
        if own:
            conn.close()
