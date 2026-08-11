# -*- coding: utf-8 -*-
"""
peraturan_db.py
---------------
Basis data PERATURAN perpajakan (sumber resource #5) untuk camerad.

Diadaptasi dari repositori jakai (app/db.py, app/repo.py, app/retrieval.py,
app/index.py). Perbedaan utama vs jakai:
  * TANPA binary extension sqlite-vec. Vektor e5 disimpan sebagai BLOB pada
    tabel peraturan_vec dan cosine dihitung di Python (numpy) -> jalan mulus di
    lingkungan camerad.
  * Retrieval hybrid = FTS5 (lexical) + vektor e5 (semantik), digabung dengan
    Reciprocal Rank Fusion (RRF), sama seperti jakai.
  * Gagal-anggun: bila FTS5 tak tersedia -> LIKE; bila embedding tak tersedia ->
    FTS/LIKE saja.

Kolom relasi status (diisi parser dari HTML TKB):
  * status_terkait  : JSON daftar peraturan TERBARU yang mengubah/mencabut
    (dari kotak legenda_status), urut dari atas. Tiap item memuat tanggal,
    nomor, judul, deskripsi, source_id (ID peraturan), href, dan link absolut.
  * history_terkait : JSON daftar peraturan SEBELUMNYA (dari legenda_history).

Konkurensi (mencegah 'database is locked'):
  SQLite hanya mengizinkan SATU penulis pada satu waktu. Proses batch menahan
  transaksi tulis selama meng-embed tiap baris (e5 di CPU bisa lambat), jadi
  koneksi lain yang menulis bisa menunggu. Karena itu koneksi memakai WAL
  (pembaca tak memblok penulis), busy_timeout 30 dtk, dan synchronous=NORMAL
  agar commit lebih ringan sehingga jendela kunci lebih pendek. Hindari juga
  menjalankan dua tugas tulis berat sekaligus (mis. batch + reindex bersamaan).

Pola koneksi mengikuti modul *_db.py camerad lain (sqlite3 + WAL).
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

# Detik menunggu bila DB sedang dikunci penulis lain sebelum menyerah.
_BUSY_TIMEOUT_MS = 30000

PERATURAN_KOLOM = [
    "id", "jenis_peraturan", "nomor", "tahun", "judul", "bab", "bagian",
    "paragraf", "pasal", "ayat", "huruf", "angka", "lampiran", "isi",
    "hierarchy", "reference", "status", "valid_from", "valid_to",
    "dicabut_oleh", "diubah_oleh", "jenis_perubahan", "target_pasal",
    "status_terkait", "history_terkait",
    "kekuatan_hukum", "can_cite", "source_url", "source_file", "source_id",
]

_INT_FIELDS = ("tahun", "kekuatan_hukum", "can_cite")

# Kolom tambahan (untuk migrasi DB lama lewat ALTER TABLE ADD COLUMN).
_KOLOM_TAMBAHAN = (
    ("status_terkait", "TEXT"),
    ("history_terkait", "TEXT"),
)

IMPOR_KOLOM = [
    "file", "source_id", "kategori", "tipe", "jenis", "nomor",
    "n_unit", "status", "catatan",
]


def floor_skor():
    try:
        return float(os.environ.get("PERATURAN_FLOOR_SKOR", "0.010"))
    except Exception:
        return 0.010


def default_db_path():
    return os.environ.get("PIPELINE_PERATURAN_DB_FILE") or os.path.join(_BASE_DIR, "peraturan.db")


def connect(db_path=None):
    # timeout= memberi lapisan tunggu di sisi driver; busy_timeout menjaga di
    # sisi engine SQLite. Keduanya diset agar penulis bersabar saat DB terkunci
    # penulis lain (mis. saat batch berjalan) alih-alih langsung 'database is locked'.
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
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        _HAS_FTS = True
    except Exception:
        _HAS_FTS = False
    return _HAS_FTS


def _migrasi_kolom(conn):
    """Tambah kolom baru pada peraturan_unit bila DB dibuat versi lama."""
    try:
        ada = {r[1] for r in conn.execute("PRAGMA table_info(peraturan_unit)").fetchall()}
    except Exception:
        return
    for nama, tipe in _KOLOM_TAMBAHAN:
        if nama not in ada:
            try:
                conn.execute("ALTER TABLE peraturan_unit ADD COLUMN %s %s" % (nama, tipe))
            except Exception:
                pass


# Cache 'sudah init' per path DB supaya tiap connect() dari request UI tidak
# menjalankan ulang DDL (executescript) yang mengambil write-lock tak perlu ->
# ikut mengurangi kemungkinan 'database is locked' saat batch berjalan.
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
        CREATE TABLE IF NOT EXISTS peraturan_unit (
            id              TEXT PRIMARY KEY,
            jenis_peraturan TEXT,
            nomor           TEXT,
            tahun           INTEGER,
            judul           TEXT,
            bab             TEXT,
            bagian          TEXT,
            paragraf        TEXT,
            pasal           TEXT,
            ayat            TEXT,
            huruf           TEXT,
            angka           TEXT,
            lampiran        TEXT,
            isi             TEXT,
            hierarchy       TEXT,
            reference       TEXT,
            status          TEXT DEFAULT 'berlaku',
            valid_from      TEXT,
            valid_to        TEXT,
            dicabut_oleh    TEXT,
            diubah_oleh     TEXT,
            jenis_perubahan TEXT,
            target_pasal    TEXT,
            status_terkait  TEXT,
            history_terkait TEXT,
            kekuatan_hukum  INTEGER,
            can_cite        INTEGER DEFAULT 1,
            source_url      TEXT,
            source_file     TEXT,
            source_id       TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_per_nomor ON peraturan_unit(jenis_peraturan, nomor);
        CREATE INDEX IF NOT EXISTS idx_per_status ON peraturan_unit(status);
        CREATE INDEX IF NOT EXISTS idx_per_source ON peraturan_unit(source_id);

        CREATE TABLE IF NOT EXISTS peraturan_vec (
            id  TEXT PRIMARY KEY,
            dim INTEGER,
            emb BLOB
        );

        CREATE TABLE IF NOT EXISTS impor_log (
            file       TEXT PRIMARY KEY,
            source_id  TEXT,
            kategori   TEXT,
            tipe       TEXT,
            jenis      TEXT,
            nomor      TEXT,
            n_unit     INTEGER DEFAULT 0,
            status     TEXT,
            catatan    TEXT,
            ts         TEXT DEFAULT (datetime('now'))
        );
        """
    )
    _migrasi_kolom(conn)
    if _fts_available(conn):
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS peraturan_fts "
                "USING fts5(id UNINDEXED, judul, isi, "
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
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM peraturan_vec").fetchone()
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
        rows = conn.execute("SELECT id, emb FROM peraturan_vec").fetchall()
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
    for k in PERATURAN_KOLOM:
        v = data.get(k)
        if isinstance(v, str) and v.strip() == "":
            v = None
        if k in _INT_FIELDS and v is not None and v != "":
            try:
                v = int(v)
            except Exception:
                v = None
        out[k] = v
    if not out.get("id"):
        raise ValueError("peraturan wajib punya 'id'")
    if out.get("status") is None:
        out["status"] = "berlaku"
    if out.get("can_cite") is None:
        out["can_cite"] = 1
    return out


def _sync_fts(conn, id_, judul, isi):
    if not _fts_available(conn):
        return
    try:
        conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
        conn.execute(
            "INSERT INTO peraturan_fts(id, judul, isi) VALUES (?, ?, ?)",
            (id_, judul or "", isi or ""),
        )
    except Exception:
        pass


def _sync_vec(conn, id_, teks):
    try:
        conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
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
        "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?, ?, ?)",
        (id_, int(len(vec)), blob),
    )
    _vec_cache_clear()
    return True


def upsert_peraturan(data, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        d = _norm(data)
        cols = PERATURAN_KOLOM
        ph = ",".join("?" for _ in cols)
        updates = ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "id")
        conn.execute(
            "INSERT INTO peraturan_unit(%s) VALUES (%s) "
            "ON CONFLICT(id) DO UPDATE SET %s, updated_at=datetime('now')"
            % (",".join(cols), ph, updates),
            tuple(d[c] for c in cols),
        )
        _sync_fts(conn, d["id"], d.get("judul"), d.get("isi"))
        vec_ok = _sync_vec(conn, d["id"], "%s %s" % (d.get("judul") or "", d.get("isi") or ""))
        conn.commit()
        return {"id": d["id"], "vec_ok": vec_ok}
    finally:
        if own:
            conn.close()


def delete_peraturan(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        conn.execute("DELETE FROM peraturan_unit WHERE id = ?", (id_,))
        if _fts_available(conn):
            conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
        conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
        conn.commit()
        _vec_cache_clear()
    finally:
        if own:
            conn.close()


# ----------------------------------------------------------------------- read
def get_peraturan(id_, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute("SELECT * FROM peraturan_unit WHERE id = ?", (id_,)).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def induk_info(source_id, conn=None):
    """Identitas peraturan INDUK dari DB berdasarkan source_id.

    Dipakai batch untuk menautkan lampiran yang diproses pada run TERPISAH
    (mis. folder khusus OCR) ke peraturan induk yang SUDAH lebih dulu diimpor,
    tanpa perlu induk HTML ikut di folder yang sama. Baris non-lampiran (dan
    yang punya pasal) diprioritaskan sebagai perwakilan identitas. Kembalikan
    dict ringkas (termasuk status_terkait/history_terkait yang sudah berupa
    JSON string siap pakai) atau None bila tak ada.
    """
    if not source_id:
        return None
    own = conn is None
    conn = conn or init_db(connect())
    try:
        r = conn.execute(
            "SELECT id, jenis_peraturan, nomor, tahun, judul, valid_from, status, "
            "       status_terkait, history_terkait, source_url "
            "FROM peraturan_unit WHERE source_id = ? "
            "ORDER BY (CASE WHEN lampiran IS NULL OR TRIM(lampiran)='' THEN 0 ELSE 1 END), "
            "         (CASE WHEN pasal IS NOT NULL AND TRIM(pasal)<>'' THEN 0 ELSE 1 END) "
            "LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(r) if r else None
    finally:
        if own:
            conn.close()


def _is_lampiran(u):
    return bool((u.get("lampiran") or "").strip()) and not (u.get("pasal") or "").strip()


def _pasal_key(u):
    is_lamp = 1 if _is_lampiran(u) else 0
    m = re.match(r"(\d+)([A-Za-z]*)", str(u.get("pasal") or ""))
    num = int(m.group(1)) if m else 10 ** 9
    suf = m.group(2) if m else ""
    ma = re.match(r"(\d+)([a-z]*)", str(u.get("ayat") or ""))
    ay = int(ma.group(1)) if ma else 0
    return (is_lamp, num, suf, ay, str(u.get("id") or ""))


def peraturan_tersusun(nomor, jenis=None, conn=None):
    """Semua unit satu peraturan, terurut: pasal/ayat lalu lampiran di bawah."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if jenis:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE nomor=? AND jenis_peraturan=?",
                (nomor, jenis),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE nomor=?", (nomor,)
            ).fetchall()
        return sorted((dict(r) for r in rows), key=_pasal_key)
    finally:
        if own:
            conn.close()


def list_peraturan_grouped(q="", jenis="", status="", lampiran="",
                           limit=200, offset=0, conn=None):
    """Daftar peraturan (dikelompokkan per jenis+nomor) dengan filter + paging."""
    own = conn is None
    conn = conn or init_db(connect())
    try:
        where, args = [], []
        if q:
            where.append("(nomor LIKE ? OR judul LIKE ?)")
            args += ["%" + q + "%", "%" + q + "%"]
        if jenis:
            where.append("jenis_peraturan = ?")
            args.append(jenis)
        if status:
            where.append("status = ?")
            args.append(status)
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        having = ""
        if lampiran == "ada":
            having = "HAVING n_lampiran > 0"
        elif lampiran == "tidak":
            having = "HAVING n_lampiran = 0"
        base = (
            "SELECT jenis_peraturan, nomor, MAX(tahun) AS tahun, "
            "COUNT(*) AS n_unit, "
            "SUM(CASE WHEN lampiran IS NOT NULL AND TRIM(lampiran)<>'' "
            "    THEN 1 ELSE 0 END) AS n_lampiran, "
            "MIN(status) AS status, MIN(judul) AS judul, "
            "MIN(source_id) AS source_id, MAX(source_url) AS source_url "
            "FROM peraturan_unit " + wsql + " "
            "GROUP BY jenis_peraturan, nomor " + having
        )
        all_rows = conn.execute(base, tuple(args)).fetchall()
        total = len(all_rows)
        items = [dict(r) for r in all_rows]
        items.sort(key=lambda r: (-(r["tahun"] or 0), r["jenis_peraturan"] or "", r["nomor"] or ""))
        items = items[offset: offset + limit]
        return {"items": items, "total": total}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------- update status
def bulk_update_status(status, keys=None, source_ids=None, extra=None, conn=None):
    if status not in ("berlaku", "dicabut", "diubah"):
        raise ValueError("status tidak valid: %s" % status)
    own = conn is None
    conn = conn or init_db(connect())
    try:
        extra = extra or {}
        set_cols = ["status = ?"]
        set_args = [status]
        for k in ("dicabut_oleh", "diubah_oleh", "jenis_perubahan", "valid_to"):
            if extra.get(k) is not None:
                set_cols.append("%s = ?" % k)
                set_args.append(extra[k])
        set_sql = ", ".join(set_cols) + ", updated_at = datetime('now')"
        n_unit = n_per = 0
        for sid in (source_ids or []):
            cur = conn.execute(
                "UPDATE peraturan_unit SET %s WHERE source_id = ?" % set_sql,
                (*set_args, sid),
            )
            if cur.rowcount:
                n_unit += cur.rowcount
                n_per += 1
        for k in (keys or []):
            cur = conn.execute(
                "UPDATE peraturan_unit SET %s WHERE jenis_peraturan = ? AND nomor = ?" % set_sql,
                (*set_args, k.get("jenis") or k.get("jenis_peraturan"), k["nomor"]),
            )
            if cur.rowcount:
                n_unit += cur.rowcount
                n_per += 1
        conn.commit()
        return {"status": status, "unit_diubah": n_unit, "peraturan_diubah": n_per}
    finally:
        if own:
            conn.close()


def bulk_delete(keys, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        n = 0
        for k in keys:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM peraturan_unit WHERE jenis_peraturan=? AND nomor=?",
                (k.get("jenis") or k.get("jenis_peraturan"), k["nomor"]),
            ).fetchall()]
            for id_ in ids:
                conn.execute("DELETE FROM peraturan_unit WHERE id = ?", (id_,))
                if _fts_available(conn):
                    conn.execute("DELETE FROM peraturan_fts WHERE id = ?", (id_,))
                conn.execute("DELETE FROM peraturan_vec WHERE id = ?", (id_,))
                n += 1
        conn.commit()
        _vec_cache_clear()
        return {"unit_dihapus": n}
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
                "INSERT INTO impor_log(%s) VALUES (%s) "
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


def list_impor_log(status="", limit=500, conn=None):
    own = conn is None
    conn = conn or init_db(connect())
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM impor_log WHERE status = ? ORDER BY kategori, file LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM impor_log ORDER BY kategori, file LIMIT ?", (limit,)
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

        LAMP = "lampiran IS NOT NULL AND TRIM(lampiran)<>''"
        KEY = "jenis_peraturan || '|' || nomor"
        out = {
            "total_peraturan": _c("SELECT COUNT(DISTINCT %s) FROM peraturan_unit" % KEY),
            "total_unit": _c("SELECT COUNT(*) FROM peraturan_unit"),
            "total_pasal": _c("SELECT COUNT(*) FROM peraturan_unit WHERE pasal IS NOT NULL AND TRIM(pasal)<>''"),
            "total_lampiran_unit": _c("SELECT COUNT(*) FROM peraturan_unit WHERE %s" % LAMP),
            "total_vec": _c("SELECT COUNT(*) FROM peraturan_vec"),
        }
        srow = conn.execute("SELECT status, COUNT(*) AS n FROM peraturan_unit GROUP BY status").fetchall()
        out["status"] = {r["status"]: r["n"] for r in srow}
        has_log = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='impor_log'"
        ).fetchone()
        triase = {}
        if has_log:
            frow = conn.execute("SELECT status, COUNT(*) AS n FROM impor_log GROUP BY status").fetchall()
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
            "SELECT id FROM peraturan_unit WHERE " + where + " LIMIT ?",
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
            "SELECT id FROM peraturan_fts WHERE peraturan_fts MATCH ? ORDER BY rank LIMIT ?",
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


def search(query, k=10, status_list=("berlaku",), conn=None):
    """Hybrid FTS5 + vektor e5, digabung RRF. Kembalikan list dict + 'skor'."""
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
                "SELECT * FROM peraturan_unit WHERE id IN (%s) AND status IN (%s)" % (id_ph, st_ph),
                (*scores.keys(), *st),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM peraturan_unit WHERE id IN (%s)" % id_ph,
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
        rows = conn.execute("SELECT id, judul, isi FROM peraturan_unit").fetchall()
        ids = [r["id"] for r in rows]
        texts = [((r["judul"] or "") + " " + (r["isi"] or "")).strip() for r in rows]
        n = 0
        for i in range(0, len(ids), batch):
            chunk_ids = ids[i:i + batch]
            chunk_txt = texts[i:i + batch]
            arr = psem.embed_passages(chunk_txt)
            if arr is None:
                continue
            for j, id_ in enumerate(chunk_ids):
                v = arr[j]
                conn.execute("DELETE FROM peraturan_vec WHERE id=?", (id_,))
                conn.execute(
                    "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?,?,?)",
                    (id_, int(len(v)), psem.to_blob(v)),
                )
                n += 1
            conn.commit()
        _vec_cache_clear()
        return {"ok": True, "n": n}
    finally:
        if own:
            conn.close()
