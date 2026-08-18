# -*- coding: utf-8 -*-
"""pipeline_store.py — Penyimpanan persisten (SQLite) untuk pipeline
"Analisis Dialogflow" (menu DIALOGFLOW).

Menggantikan model lama yang berbasis folder _runs/<run>/ berisi state.json +
file Excel. Sekarang DB menjadi SUMBER KEBENARAN:

  - datasets      : satu "dataset" = 1 rentang tanggal log + bahasa
                    (mis. 2026-08-01..2026-08-15 / id). Menggantikan "Run ID".
  - step_artifact : artefak terbaru per step (bytes disimpan sebagai BLOB).
                    Excel/JSON/ZIP di-generate/di-regenerasi dari sini saat
                    diunduh, sehingga tetap identik dengan sebelumnya.
  - step_edit     : baris editan terstruktur (Step 6 & 9) dengan KUNCI BISNIS
                    stabil (bukan nomor baris Excel). Upsert — versi terbaru
                    menang. Ini memperbaiki bug simpan→edit→simpan.

Desain kunci:
  * Reset = SOFT (status='archived'), bukan hapus — masih bisa dimuat ulang.
  * Ulang step / edit ulang = upsert (menimpa) pada dataset yang sama.
  * Hanya stdlib (sqlite3). DB default: pipeline_store.db
    (env PIPELINE_STORE_DB_FILE).

Modul ini hanya lapisan data; integrasi ke pipeline ada di pipeline_helpers.py.
"""
import os
import json
import sqlite3
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def default_db_path():
    return os.environ.get("PIPELINE_STORE_DB_FILE") or os.path.join(_BASE_DIR, "pipeline_store.db")


def _now():
    return _dt.datetime.now().isoformat()


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=8000;")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dkey         TEXT UNIQUE,
            range_start  TEXT,
            range_end    TEXT,
            lang         TEXT,
            label        TEXT,
            status       TEXT DEFAULT 'active',
            ngrok_url    TEXT DEFAULT '',
            meta         TEXT DEFAULT '{}',
            created_at   TEXT,
            updated_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ds_status ON datasets(status);
        CREATE INDEX IF NOT EXISTS idx_ds_updated ON datasets(updated_at);

        CREATE TABLE IF NOT EXISTS step_artifact (
            dataset_id   INTEGER,
            step         INTEGER,
            ext          TEXT,
            name         TEXT,
            mime         TEXT,
            size         INTEGER,
            data         BLOB,
            summary      TEXT,
            updated_at   TEXT,
            PRIMARY KEY (dataset_id, step)
        );

        CREATE TABLE IF NOT EXISTS step_edit (
            dataset_id   INTEGER,
            step         INTEGER,
            biz_key      TEXT,
            payload      TEXT,
            updated_at   TEXT,
            PRIMARY KEY (dataset_id, step, biz_key)
        );
        CREATE INDEX IF NOT EXISTS idx_edit_ds_step ON step_edit(dataset_id, step);
        """
    )
    conn.commit()
    return conn


def _conn_init():
    return init_db(connect())


# =============================================================
# Dataset (pengganti "Run ID")
# =============================================================
def make_key(range_start, range_end, lang):
    rs = (range_start or "").strip()[:10]
    re_ = (range_end or "").strip()[:10]
    lg = (lang or "id").strip().lower()
    return "%s__%s__%s" % (rs, re_, lg)


def _row_to_dataset(r):
    if r is None:
        return None
    d = dict(r)
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except Exception:
        d["meta"] = {}
    return d


def get_dataset(conn, dataset_id):
    r = conn.execute("SELECT * FROM datasets WHERE id=?", (int(dataset_id),)).fetchone()
    return _row_to_dataset(r)


def get_dataset_by_key(conn, dkey):
    r = conn.execute("SELECT * FROM datasets WHERE dkey=?", (dkey,)).fetchone()
    return _row_to_dataset(r)


def get_or_create_dataset(conn, range_start, range_end, lang, label=None, activate=True):
    dkey = make_key(range_start, range_end, lang)
    ex = get_dataset_by_key(conn, dkey)
    now = _now()
    if ex:
        if activate:
            conn.execute("UPDATE datasets SET status='active', updated_at=? WHERE id=?",
                         (now, ex["id"]))
            conn.commit()
            ex = get_dataset(conn, ex["id"])
        return ex
    if not label:
        label = "%s s/d %s (%s)" % ((range_start or "?")[:10], (range_end or "?")[:10],
                                    (lang or "id").lower())
    conn.execute(
        "INSERT INTO datasets(dkey,range_start,range_end,lang,label,status,ngrok_url,meta,created_at,updated_at) "
        "VALUES(?,?,?,?,?,'active','','{}',?,?)",
        (dkey, (range_start or "")[:10], (range_end or "")[:10], (lang or "id").lower(),
         label, now, now),
    )
    conn.commit()
    return get_dataset_by_key(conn, dkey)


def touch(conn, dataset_id, activate=True):
    if activate:
        conn.execute("UPDATE datasets SET status='active', updated_at=? WHERE id=?",
                     (_now(), int(dataset_id)))
    else:
        conn.execute("UPDATE datasets SET updated_at=? WHERE id=?", (_now(), int(dataset_id)))
    conn.commit()


def get_active_dataset(conn):
    """Dataset aktif = paling baru disentuh & belum diarsip (untuk auto-muat)."""
    r = conn.execute(
        "SELECT * FROM datasets WHERE status='active' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    return _row_to_dataset(r)


def list_datasets(conn, include_archived=True, limit=200):
    q = "SELECT * FROM datasets"
    if not include_archived:
        q += " WHERE status='active'"
    q += " ORDER BY updated_at DESC LIMIT ?"
    return [_row_to_dataset(r) for r in conn.execute(q, (int(limit),)).fetchall()]


def archive_dataset(conn, dataset_id):
    """Soft reset: tandai arsip. Data tetap ada & bisa dimuat ulang."""
    conn.execute("UPDATE datasets SET status='archived', updated_at=? WHERE id=?",
                 (_now(), int(dataset_id)))
    conn.commit()
    return {"archived": True, "dataset_id": int(dataset_id)}


def activate_dataset(conn, dataset_id):
    conn.execute("UPDATE datasets SET status='active', updated_at=? WHERE id=?",
                 (_now(), int(dataset_id)))
    conn.commit()
    return get_dataset(conn, dataset_id)


def delete_dataset(conn, dataset_id):
    """Hapus permanen (dipakai bila benar-benar perlu, bukan reset biasa)."""
    did = int(dataset_id)
    conn.execute("DELETE FROM step_artifact WHERE dataset_id=?", (did,))
    conn.execute("DELETE FROM step_edit WHERE dataset_id=?", (did,))
    conn.execute("DELETE FROM datasets WHERE id=?", (did,))
    conn.commit()
    return {"deleted": True, "dataset_id": did}


def set_ngrok(conn, dataset_id, url):
    conn.execute("UPDATE datasets SET ngrok_url=?, updated_at=? WHERE id=?",
                 (url or "", _now(), int(dataset_id)))
    conn.commit()


def get_ngrok(conn, dataset_id):
    r = conn.execute("SELECT ngrok_url FROM datasets WHERE id=?", (int(dataset_id),)).fetchone()
    return (r["ngrok_url"] if r else "") or ""


# =============================================================
# Artefak per step (bytes = sumber kebenaran)
# =============================================================
def save_artifact(conn, dataset_id, step, ext, name, mime, data_bytes, summary=None):
    if isinstance(data_bytes, str):
        data_bytes = data_bytes.encode("utf-8")
    conn.execute(
        "INSERT INTO step_artifact(dataset_id,step,ext,name,mime,size,data,summary,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(dataset_id,step) DO UPDATE SET "
        "ext=excluded.ext, name=excluded.name, mime=excluded.mime, size=excluded.size, "
        "data=excluded.data, summary=excluded.summary, updated_at=excluded.updated_at",
        (int(dataset_id), int(step), ext, name, mime, len(data_bytes),
         sqlite3.Binary(data_bytes), json.dumps(summary or {}, ensure_ascii=False), _now()),
    )
    touch(conn, dataset_id)
    return get_artifact_meta(conn, dataset_id, step)


def get_artifact(conn, dataset_id, step):
    """Kembalikan metadata + bytes ('data')."""
    r = conn.execute("SELECT * FROM step_artifact WHERE dataset_id=? AND step=?",
                     (int(dataset_id), int(step))).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["summary"] = json.loads(d.get("summary") or "{}")
    except Exception:
        d["summary"] = {}
    if d.get("data") is not None:
        d["data"] = bytes(d["data"])
    return d


def get_artifact_bytes(conn, dataset_id, step):
    r = conn.execute("SELECT data FROM step_artifact WHERE dataset_id=? AND step=?",
                     (int(dataset_id), int(step))).fetchone()
    if not r or r["data"] is None:
        return None
    return bytes(r["data"])


def get_artifact_meta(conn, dataset_id, step):
    """Metadata artefak TANPA bytes (untuk merekonstruksi 'state')."""
    r = conn.execute(
        "SELECT step,ext,name,mime,size,summary,updated_at FROM step_artifact "
        "WHERE dataset_id=? AND step=?", (int(dataset_id), int(step))).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["summary"] = json.loads(d.get("summary") or "{}")
    except Exception:
        d["summary"] = {}
    return d


def list_artifacts(conn, dataset_id):
    rows = conn.execute(
        "SELECT step,ext,name,mime,size,summary,updated_at FROM step_artifact "
        "WHERE dataset_id=? ORDER BY step", (int(dataset_id),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d.get("summary") or "{}")
        except Exception:
            d["summary"] = {}
        out.append(d)
    return out


def delete_artifact(conn, dataset_id, step):
    conn.execute("DELETE FROM step_artifact WHERE dataset_id=? AND step=?",
                 (int(dataset_id), int(step)))
    conn.commit()


# =============================================================
# Edit terstruktur (Step 6 & 9) — kunci bisnis stabil, upsert
# =============================================================
def upsert_edit(conn, dataset_id, step, biz_key, payload):
    conn.execute(
        "INSERT INTO step_edit(dataset_id,step,biz_key,payload,updated_at) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(dataset_id,step,biz_key) DO UPDATE SET "
        "payload=excluded.payload, updated_at=excluded.updated_at",
        (int(dataset_id), int(step), str(biz_key),
         json.dumps(payload or {}, ensure_ascii=False), _now()),
    )


def upsert_edits(conn, dataset_id, step, items):
    """items: list of (biz_key, payload_dict). Satu transaksi."""
    for biz_key, payload in items:
        upsert_edit(conn, dataset_id, step, biz_key, payload)
    touch(conn, dataset_id)
    conn.commit()


def list_edits(conn, dataset_id, step):
    """Kembalikan {biz_key: payload_dict}."""
    rows = conn.execute(
        "SELECT biz_key,payload FROM step_edit WHERE dataset_id=? AND step=?",
        (int(dataset_id), int(step))).fetchall()
    out = {}
    for r in rows:
        try:
            out[r["biz_key"]] = json.loads(r["payload"] or "{}")
        except Exception:
            out[r["biz_key"]] = {}
    return out


def clear_edits(conn, dataset_id, step):
    conn.execute("DELETE FROM step_edit WHERE dataset_id=? AND step=?",
                 (int(dataset_id), int(step)))
    conn.commit()


# =============================================================
# Rekonstruksi "state" (kompatibel dgn bentuk lama pipeline_helpers)
# =============================================================
def build_state(conn, dataset):
    """Bentuk mirip state.json lama: {run, ngrok_url, steps:{"<n>":{...}}}.
    'file' bersifat sintetis (stepN.ext) untuk kompatibilitas kode step yang
    masih memeriksa keberadaan file di cache disk.
    """
    if not dataset:
        return {"run": "", "ngrok_url": "", "steps": {}}
    steps = {}
    for a in list_artifacts(conn, dataset["id"]):
        n = a["step"]
        ext = a.get("ext") or "bin"
        steps[str(n)] = {
            "status": "done",
            "file": "step%s.%s" % (n, ext),
            "name": a.get("name") or "",
            "ext": ext,
            "mime": a.get("mime") or "",
            "size": a.get("size") or 0,
            "summary": a.get("summary") or {},
            "at": a.get("updated_at") or "",
        }
    return {
        "run": dataset.get("dkey", ""),
        "dataset_id": dataset.get("id"),
        "ngrok_url": dataset.get("ngrok_url", "") or "",
        "label": dataset.get("label", ""),
        "range_start": dataset.get("range_start", ""),
        "range_end": dataset.get("range_end", ""),
        "lang": dataset.get("lang", ""),
        "status": dataset.get("status", "active"),
        "steps": steps,
    }
