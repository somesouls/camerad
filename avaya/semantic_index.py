# -*- coding: utf-8 -*-
"""avaya/semantic_index.py — index vektor semantik percakapan AWE (bge-m3).

Mengganti pencarian AWE brute-force (scan LIKE hingga 400 baris transkrip tiap
query; cold-scan pernah ~236 dtk + pemanasan ~232 dtk) dengan pencarian vektor
O(1)-query. Pola mengikuti sosmed/semantic_index.py:
  * Vektor bge-m3 (1024-d) disimpan BLOB, cosine dihitung di numpy.
  * Embedder dipakai ULANG dari peraturan.semantic (psem) -> model SAMA dengan
    peraturan/sop/sosmed, jadi TIDAK menambah VRAM.

TIDAK mengubah avaya/db.py. Index di FILE TERPISAH (default: di samping
avaya.db => awe_vec.db). Kunci = sid (transkrip TERBARU per sid dipakai,
konsisten dgn avaya.db.get_transcript). Teks yang di-embed = giliran PELANGGAN
+ topik + jenis_layanan + mapped_intent sehingga kueri pengguna match ke
percakapan yang keluhannya mirip. Inkremental via txt_hash. Gagal-anggun penuh:
numpy/model/DB tak siap -> search_ids()=[] (pemanggil jatuh ke brute-force).

Env: AWE_INDEX(1), AWE_VEC_DB_FILE(<dir avaya.db>/awe_vec.db),
     AWE_INDEX_LIMIT(100000), AWE_INDEX_BATCH(64).
"""
import os
import json as _json
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
    import avaya.db as adb
except Exception:            # pragma: no cover
    adb = None

_BUSY_MS = 30000
_LOCK = threading.RLock()
_CACHE = {"sig": None, "ids": None, "mat": None}


def _enabled():
    return str(os.environ.get("AWE_INDEX", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _limit():
    try:
        return int(os.environ.get("AWE_INDEX_LIMIT", "100000") or 100000)
    except Exception:
        return 100000


def _batch():
    try:
        return int(os.environ.get("AWE_INDEX_BATCH", "64") or 64)
    except Exception:
        return 64


def _db_path():
    p = os.environ.get("AWE_VEC_DB_FILE")
    if p:
        return p
    base = None
    try:
        base = os.path.dirname(adb.default_db_path())
    except Exception:
        base = None
    return os.path.join(base or os.getcwd(), "awe_vec.db")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=_BUSY_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=%d;" % _BUSY_MS)
    return conn


def _init(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS awe_vec ("
        "sid TEXT PRIMARY KEY, dim INTEGER, txt_hash TEXT, emb BLOB)")
    conn.commit()
    return conn


def _hash(s):
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()


def _clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n]


def _key_text(transkrip_json, topik, jenis, intent):
    """Teks kunci utk embed: giliran PELANGGAN + label. Fail-open ''."""
    cust = []
    try:
        tx = _json.loads(transkrip_json) if transkrip_json else None
    except Exception:
        tx = None
    if isinstance(tx, list):
        for m in tx:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "")
            text = m.get("text", "")
            try:
                is_agent = adb._is_agent(role, text) if adb else True
            except Exception:
                is_agent = True
            if not is_agent and str(text).strip():
                cust.append(str(text).strip())
            if len(cust) >= 12:
                break
    label = " ".join(x for x in (topik, jenis, intent) if x)
    body = " ".join(cust)
    return (_clip(body, 1200) + " " + _clip(label, 200)).strip()


def _cache_clear():
    _CACHE["sig"] = None
    _CACHE["ids"] = None
    _CACHE["mat"] = None


def _sig(conn):
    try:
        r = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM awe_vec").fetchone()
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
            rows = conn.execute("SELECT sid, emb FROM awe_vec").fetchall()
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
            ids.append(str(r["sid"]))
            vecs.append(v)
        mat = np.vstack(vecs) if vecs else None
        _CACHE["sig"] = sig
        _CACHE["ids"] = ids
        _CACHE["mat"] = mat
        return ids, mat


def search_ids(query, k=30):
    """Kembalikan [(sid, skor_cosine), ...] top-k. [] bila tak siap/kosong."""
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
    """(Re)bangun index inkremental. Hanya embed sid baru/berubah."""
    if not _enabled() or np is None or psem is None or adb is None:
        return {"ok": False, "n": 0, "reason": "nonaktif/dependensi"}
    try:
        if not psem.is_available():
            return {"ok": False, "n": 0, "reason": "model embedding tak tersedia"}
    except Exception:
        return {"ok": False, "n": 0, "reason": "model err"}
    with _LOCK:
        # 1) kumpulkan sid unik (transkrip TERBARU per sid) dari avaya.db
        try:
            c = adb.init_db(adb.connect())
            try:
                rows = c.execute(
                    "SELECT sid, transkrip_json, topik, mapped_intent, "
                    "jenis_layanan FROM awe_conversations "
                    "WHERE transkrip_json IS NOT NULL AND transkrip_json!='' "
                    "ORDER BY rowid DESC").fetchall()
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception as e:
            return {"ok": False, "n": 0, "reason": "avaya.db: %s" % str(e)[:120]}
        seen = {}
        lim = _limit()
        for r in rows:
            sid = str(r["sid"] or "").strip()
            if not sid or sid in seen:
                continue
            txt = _key_text(r["transkrip_json"], r["topik"],
                            r["jenis_layanan"], r["mapped_intent"])
            if not txt:
                continue
            seen[sid] = txt
            if len(seen) >= lim:
                break
        # 2) tulis inkremental ke awe_vec
        try:
            conn = _init(_connect())
        except Exception as e:
            return {"ok": False, "n": 0, "reason": "db: %s" % str(e)[:120]}
        try:
            have = {}
            for r in conn.execute("SELECT sid, txt_hash FROM awe_vec").fetchall():
                have[str(r["sid"])] = r["txt_hash"]
            todo = []
            for sid, txt in seen.items():
                h = _hash(txt)
                if force or have.get(sid) != h:
                    todo.append((sid, h, txt))
            n = 0
            bs = _batch()
            for i in range(0, len(todo), bs):
                chunk = todo[i:i + bs]
                arr = psem.embed_passages([t for (_, _, t) in chunk])
                if arr is None:
                    continue
                for j, (sid, h, _t) in enumerate(chunk):
                    try:
                        v = arr[j]
                        conn.execute("DELETE FROM awe_vec WHERE sid=?", (sid,))
                        conn.execute(
                            "INSERT INTO awe_vec(sid, dim, txt_hash, emb) "
                            "VALUES(?,?,?,?)",
                            (sid, int(len(v)), h, psem.to_blob(v)))
                        n += 1
                    except Exception:
                        pass
                conn.commit()
            # 3) prune sid yang sudah tak ada
            try:
                old = [str(r["sid"]) for r in conn.execute(
                    "SELECT sid FROM awe_vec").fetchall()]
                gone = [s for s in old if s not in seen]
                for s in gone:
                    conn.execute("DELETE FROM awe_vec WHERE sid=?", (s,))
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
        return {"ok": True, "n": n, "total": len(seen)}


def stats():
    try:
        conn = _init(_connect())
        try:
            n = conn.execute("SELECT COUNT(*) FROM awe_vec").fetchone()[0]
        finally:
            conn.close()
        return {"vec": int(n or 0), "db": _db_path()}
    except Exception as e:
        return {"vec": 0, "db": _db_path(), "error": str(e)[:120]}
