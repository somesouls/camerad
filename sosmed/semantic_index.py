# -*- coding: utf-8 -*-
"""sosmed/semantic_index.py — index vektor semantik FAQ Sosmed (bge-m3).

Tujuan: mengganti pencarian sosmed brute-force O(N) (scan hingga 2000 baris +
stemming Sastrawi per baris tiap query) dengan pencarian vektor O(1)-query. Ini
yang membuat sumber 'sosmed' pernah memakan 185/115 dtk pada query pertama.

Pola mengikuti sop_db.py:
  * Vektor bge-m3 (1024-d) disimpan sebagai BLOB, cosine dihitung di numpy.
  * Embedder dipakai ULANG dari peraturan.semantic (psem) -> model SAMA dengan
    peraturan/sop, jadi TIDAK menambah VRAM.

Modul ini TIDAK mengubah sosmed/db.py. Index disimpan di FILE TERPISAH
(default: di samping sosmed.db => sosmed_vec.db) dan dibangun dari API yang ada,
sdb.faq_pairs(only_answered=True). Teks yang di-embed = 'pertanyaan + topik'
(persis haystack skor _ctx_sosmed_v2) sehingga hasil setara tanpa scan penuh.

Inkremental: tiap baris disimpan bersama txt_hash. build() hanya meng-embed
baris BARU/berubah -> aman dijalankan tiap boot & setelah ingest harian.

Gagal-anggun penuh: numpy/model/DB tak tersedia -> search_ids()=[] sehingga
pemanggil jatuh ke jalur brute-force lama.

Env:
  SOSMED_INDEX=0        matikan (default 1).
  SOSMED_VEC_DB_FILE    path file index (default <dir sosmed.db>/sosmed_vec.db).
  SOSMED_INDEX_LIMIT    maks baris FAQ diindeks (default 100000).
  SOSMED_INDEX_BATCH    batch embed (default 64).
"""
import os
import hashlib
import sqlite3
import threading

try:
    import numpy as np
except Exception:            # pragma: no cover
    np = None

try:
    import peraturan.semantic as psem
except Exception:            # pragma: no cover
    psem = None

try:
    import sosmed.db as sdb
except Exception:            # pragma: no cover
    sdb = None

_BUSY_MS = 30000
_LOCK = threading.RLock()
_CACHE = {"sig": None, "ids": None, "mat": None}


def _enabled():
    return str(os.environ.get("SOSMED_INDEX", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _limit():
    try:
        return int(os.environ.get("SOSMED_INDEX_LIMIT", "100000") or 100000)
    except Exception:
        return 100000


def _batch():
    try:
        return int(os.environ.get("SOSMED_INDEX_BATCH", "64") or 64)
    except Exception:
        return 64


def _db_path():
    p = os.environ.get("SOSMED_VEC_DB_FILE")
    if p:
        return p
    base = None
    try:
        base = os.path.dirname(sdb.default_db_path())
    except Exception:
        base = None
    return os.path.join(base or os.getcwd(), "sosmed_vec.db")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=_BUSY_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_MS)
    return conn


def _init(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sosmed_vec ("
        "id INTEGER PRIMARY KEY, dim INTEGER, txt_hash TEXT, emb BLOB)")
    conn.commit()
    return conn


def _hash(s):
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()


def _key_text(p):
    return ((p.get("pertanyaan") or "") + " " + str(p.get("topik") or "")).strip()


def _cache_clear():
    _CACHE["sig"] = None
    _CACHE["ids"] = None
    _CACHE["mat"] = None


def _sig(conn):
    try:
        r = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id),0) FROM sosmed_vec").fetchone()
        return (int(r[0]), int(r[1]))
    except Exception:
        return (0, 0)


def _load():
    if np is None or psem is None:
        return [], None
    with _LOCK:
        try:
            conn = _init(_connect())
        except Exception:
            return [], None
        try:
            sig = _sig(conn)
            if _CACHE["sig"] == sig and _CACHE["mat"] is not None:
                return _CACHE["ids"], _CACHE["mat"]
            rows = conn.execute("SELECT id, emb FROM sosmed_vec").fetchall()
        except Exception:
            return [], None
        finally:
            try:
                conn.close()
            except Exception:
                pass
        ids, vecs = [], []
        for r in rows:
            v = psem.from_blob(r["emb"])
            if v is None:
                continue
            ids.append(int(r["id"]))
            vecs.append(v)
        mat = np.vstack(vecs) if vecs else None
        _CACHE["sig"] = sig
        _CACHE["ids"] = ids
        _CACHE["mat"] = mat
        return ids, mat


def search_ids(query, k=30):
    """Kembalikan [(id, skor_cosine), ...] top-k. [] bila tak siap/kosong."""
    if not _enabled() or np is None or psem is None:
        return []
    q = (query or "").strip()
    if not q:
        return []
    try:
        qv = psem.embed_query(q)
    except Exception:
        qv = None
    if qv is None:
        return []
    ids, mat = _load()
    if mat is None or not ids:
        return []
    try:
        sims = mat @ np.asarray(qv, dtype="float32")
        order = np.argsort(-sims)[:max(1, int(k))]
        return [(ids[int(i)], float(sims[int(i)])) for i in order]
    except Exception:
        return []


def build(force=False):
    """(Re)bangun index inkremental. Hanya embed baris baru/berubah.

    Aman dipanggil di thread latar saat boot & setelah ingest harian.
    """
    if not _enabled() or np is None or psem is None or sdb is None:
        return {"ok": False, "n": 0, "reason": "nonaktif/dependensi"}
    try:
        if not psem.is_available():
            return {"ok": False, "n": 0, "reason": "model embedding tak tersedia"}
    except Exception:
        return {"ok": False, "n": 0, "reason": "model err"}
    with _LOCK:
        try:
            c = sdb.init_db(sdb.connect())
            try:
                fp = sdb.faq_pairs(c, only_answered=True, limit=_limit())
                pairs = fp.get("pairs") or []
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception as e:
            return {"ok": False, "n": 0, "reason": "faq_pairs: %s" % str(e)[:120]}
        try:
            conn = _init(_connect())
        except Exception as e:
            return {"ok": False, "n": 0, "reason": "db: %s" % str(e)[:120]}
        try:
            have = {}
            for r in conn.execute("SELECT id, txt_hash FROM sosmed_vec").fetchall():
                try:
                    have[int(r["id"])] = r["txt_hash"]
                except Exception:
                    pass
            todo, cur_ids = [], set()
            for p in pairs:
                try:
                    pid = int(p.get("id"))
                except Exception:
                    continue
                txt = _key_text(p)
                if not txt:
                    continue
                cur_ids.add(pid)
                h = _hash(txt)
                if force or have.get(pid) != h:
                    todo.append((pid, h, txt))
            n = 0
            bs = _batch()
            for i in range(0, len(todo), bs):
                chunk = todo[i:i + bs]
                arr = psem.embed_passages([t for (_, _, t) in chunk])
                if arr is None:
                    continue
                for j, (pid, h, _t) in enumerate(chunk):
                    try:
                        v = arr[j]
                        conn.execute("DELETE FROM sosmed_vec WHERE id=?", (pid,))
                        conn.execute(
                            "INSERT INTO sosmed_vec(id, dim, txt_hash, emb) "
                            "VALUES(?,?,?,?)",
                            (pid, int(len(v)), h, psem.to_blob(v)))
                        n += 1
                    except Exception:
                        pass
                conn.commit()
            try:
                old = [int(r["id"]) for r in conn.execute(
                    "SELECT id FROM sosmed_vec").fetchall()]
                gone = [i for i in old if i not in cur_ids]
                for i in gone:
                    conn.execute("DELETE FROM sosmed_vec WHERE id=?", (i,))
                if gone:
                    conn.commit()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _cache_clear()
        return {"ok": True, "n": n, "total": len(cur_ids)}


def stats():
    try:
        conn = _init(_connect())
        try:
            n = conn.execute("SELECT COUNT(*) FROM sosmed_vec").fetchone()[0]
        finally:
            conn.close()
        return {"vec": int(n or 0), "db": _db_path()}
    except Exception as e:
        return {"vec": 0, "db": _db_path(), "error": str(e)[:120]}
