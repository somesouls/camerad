# -*- coding: utf-8 -*-
"""
avaya_db.py
-----------
Lapisan penyimpanan (SQLite) untuk analitik AWE Avaya (percakapan Live-Chat
user <-> bot + agent).

DISENGAJA TERPISAH dari analytics_db.py (Dialogflow):
- File DB berbeda (default: avaya.db), env override: AVAYA_DB_FILE.
- Tidak ada penggabungan dengan data Dialogflow karena tidak ada ID unik yang
  sama antar kedua sumber (sesuai keputusan desain).

Tujuan: sekali analisis AWE dijalankan, hasilnya DISIMPAN supaya analis tidak
perlu upload/analisa ulang. Setiap kali analisis selesai -> 1 baris di awe_runs
(+ ledakan percakapan ke awe_conversations untuk query per-baris).

Hanya memakai stdlib (sqlite3 + json + hashlib). Tidak butuh server database.
"""
import os
import json as _json
import sqlite3
import hashlib as _hashlib
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def default_db_path():
    return os.environ.get("AVAYA_DB_FILE") or os.path.join(_BASE_DIR, "avaya.db")


def _jkt_now_iso():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=8000;")
    return conn


def init_db(conn):
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS awe_runs (
            id            TEXT PRIMARY KEY,
            label         TEXT,
            date_min      TEXT,
            date_max      TEXT,
            total_conv    INTEGER DEFAULT 0,
            total_cust    INTEGER DEFAULT 0,
            n_files       INTEGER DEFAULT 0,
            engine        TEXT,
            build         TEXT,
            source        TEXT DEFAULT 'upload',   -- 'upload' | 'pull'
            dashboard_json TEXT,                    -- blob dashboard lengkap
            records_json  TEXT,                     -- blob records mentah
            created_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_conversations (
            run_id        TEXT,
            sid           TEXT,
            tanggal       TEXT,
            customer      TEXT,
            agent_name    TEXT,
            agent_id      TEXT,
            durasi        INTEGER DEFAULT 0,
            behavior      TEXT,
            is_returning  TEXT,
            mapped_intent TEXT,
            coverage_band TEXT,
            case_label    TEXT,
            sentiment     TEXT,
            emotion       TEXT,
            PRIMARY KEY (run_id, sid)
        );
        CREATE INDEX IF NOT EXISTS idx_awe_conv_run ON awe_conversations(run_id);
        CREATE INDEX IF NOT EXISTS idx_awe_conv_intent ON awe_conversations(mapped_intent);
        CREATE TABLE IF NOT EXISTS awe_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()
    return conn


# ---- util ekstraksi field dashboard (defensif thd variasi nama kunci) ------
def _g(d, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _make_run_id(dashboard, records):
    meta = dashboard.get("meta", {}) if isinstance(dashboard, dict) else {}
    convs = dashboard.get("conversations", []) if isinstance(dashboard, dict) else []
    sids = sorted(str(_g(c, "sid", "Sid", default="")) for c in convs if isinstance(c, dict))
    basis = "|".join([
        str(_g(meta, "date_min", "tanggal_min", default="")),
        str(_g(meta, "date_max", "tanggal_max", default="")),
        str(_g(meta, "total_conv", "total", default=len(convs))),
        ",".join(sids),
    ])
    return _hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def save_run(conn, dashboard, records=None, label=None, n_files=0, source="upload", build=None):
    """Simpan satu hasil analisis AWE. Idempoten: run_id sama -> ditimpa.

    Mengembalikan dict {id, total_conv, total_cust, date_min, date_max, new}.
    """
    if not isinstance(dashboard, dict):
        raise ValueError("dashboard harus dict")
    records = records or []
    meta = dashboard.get("meta", {}) or {}
    convs = dashboard.get("conversations", []) or []
    run_id = _make_run_id(dashboard, records)
    date_min = str(_g(meta, "date_min", "tanggal_min", default=""))
    date_max = str(_g(meta, "date_max", "tanggal_max", default=""))
    total_conv = int(_g(meta, "total_conv", "total", default=len(convs)) or len(convs))
    total_cust = int(_g(meta, "total_customers", "total_cust", default=0) or 0)
    engine = str(_g(meta, "engine", default=""))
    if not label:
        label = ("%s s/d %s" % (date_min, date_max)) if date_min or date_max else "Analisis AWE"

    cur = conn.cursor()
    exists = cur.execute("SELECT 1 FROM awe_runs WHERE id=?", (run_id,)).fetchone() is not None
    cur.execute(
        """INSERT INTO awe_runs
             (id,label,date_min,date_max,total_conv,total_cust,n_files,engine,build,source,dashboard_json,records_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             label=excluded.label, date_min=excluded.date_min, date_max=excluded.date_max,
             total_conv=excluded.total_conv, total_cust=excluded.total_cust, n_files=excluded.n_files,
             engine=excluded.engine, build=excluded.build, source=excluded.source,
             dashboard_json=excluded.dashboard_json, records_json=excluded.records_json,
             created_at=excluded.created_at
        """,
        (run_id, label, date_min, date_max, total_conv, total_cust, int(n_files or 0),
         engine, build or "", source,
         _json.dumps(dashboard, ensure_ascii=False),
         _json.dumps(records, ensure_ascii=False),
         _jkt_now_iso()),
    )
    # ledakkan percakapan
    cur.execute("DELETE FROM awe_conversations WHERE run_id=?", (run_id,))
    rows = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        rows.append((
            run_id,
            str(_g(c, "sid", "Sid", default="")),
            str(_g(c, "tanggal", "date", "start", default="")),
            str(_g(c, "customer", "pelanggan", default="")),
            str(_g(c, "agent_name", "agent", "agentName", default="")),
            str(_g(c, "agent_id", "agentId", default="")),
            int(_g(c, "durasi", "duration", "duration_seconds", default=0) or 0),
            str(_g(c, "behavior", "perilaku", default="")),
            str(_g(c, "returning", "balik", default="")),
            str(_g(c, "mapped_intent", "intent", default="")),
            str(_g(c, "coverage_band", "coverage", "band", default="")),
            str(_g(c, "case_label", "case", "kasus", default="")),
            str(_g(c, "sentiment", "sentimen", default="")),
            str(_g(c, "emotion", "emosi", default="")),
        ))
    if rows:
        cur.executemany(
            """INSERT OR REPLACE INTO awe_conversations
                 (run_id,sid,tanggal,customer,agent_name,agent_id,durasi,behavior,
                  is_returning,mapped_intent,coverage_band,case_label,sentiment,emotion)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    set_meta(conn, "last_saved_at", _jkt_now_iso())
    conn.commit()
    return {"id": run_id, "total_conv": total_conv, "total_cust": total_cust,
            "date_min": date_min, "date_max": date_max, "new": not exists}


def list_runs(conn, limit=100):
    cur = conn.cursor()
    rs = cur.execute(
        """SELECT id,label,date_min,date_max,total_conv,total_cust,n_files,engine,
                  build,source,created_at
           FROM awe_runs ORDER BY datetime(created_at) DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rs]


def get_run(conn, run_id, with_records=False):
    cur = conn.cursor()
    r = cur.execute("SELECT * FROM awe_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    out = {
        "id": d["id"], "label": d["label"], "date_min": d["date_min"],
        "date_max": d["date_max"], "total_conv": d["total_conv"],
        "total_cust": d["total_cust"], "n_files": d["n_files"],
        "engine": d["engine"], "build": d["build"], "source": d["source"],
        "created_at": d["created_at"],
        "dashboard": _json.loads(d["dashboard_json"] or "{}"),
    }
    if with_records:
        out["records"] = _json.loads(d["records_json"] or "[]")
    return out


def delete_run(conn, run_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM awe_conversations WHERE run_id=?", (run_id,))
    cur.execute("DELETE FROM awe_runs WHERE id=?", (run_id,))
    conn.commit()
    return cur.rowcount


def latest_run(conn, with_records=False):
    cur = conn.cursor()
    r = cur.execute(
        "SELECT id FROM awe_runs ORDER BY datetime(created_at) DESC LIMIT 1"
    ).fetchone()
    if not r:
        return None
    return get_run(conn, r["id"], with_records=with_records)


def stats(conn):
    cur = conn.cursor()
    row = cur.execute(
        """SELECT COUNT(*) AS runs, COALESCE(SUM(total_conv),0) AS conv,
                  MIN(date_min) AS dmin, MAX(date_max) AS dmax
           FROM awe_runs"""
    ).fetchone()
    return {"runs": row["runs"], "conversations": row["conv"],
            "date_min": row["dmin"] or "", "date_max": row["dmax"] or ""}


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO awe_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM awe_meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


if __name__ == "__main__":
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "avaya_smoke.db")
    if os.path.exists(p):
        os.remove(p)
    c = init_db(connect(p))
    dash = {"meta": {"date_min": "2026-07-01", "date_max": "2026-07-31",
                       "total_conv": 2, "total_customers": 2, "engine": "mpnet"},
            "conversations": [
                {"sid": "A1", "tanggal": "2026-07-02", "customer": "cust1",
                 "mapped_intent": "Lapor SPT", "coverage_band": "Tinggi",
                 "sentiment": "positif"},
                {"sid": "A2", "tanggal": "2026-07-05", "customer": "cust2",
                 "mapped_intent": "EFIN", "coverage_band": "Rendah",
                 "sentiment": "negatif"},
            ]}
    r = save_run(c, dash, records=[{"sid": "A1"}], n_files=1, source="upload", build="test")
    assert r["new"] is True and r["total_conv"] == 2, r
    # simpan lagi -> idempoten (bukan baru)
    r2 = save_run(c, dash, records=[{"sid": "A1"}], n_files=1)
    assert r2["new"] is False and r2["id"] == r["id"], r2
    assert len(list_runs(c)) == 1, list_runs(c)
    got = get_run(c, r["id"], with_records=True)
    assert got["dashboard"]["meta"]["total_conv"] == 2
    assert len(got["records"]) == 1
    st = stats(c)
    assert st["runs"] == 1 and st["conversations"] == 2, st
    ncols = c.execute("SELECT COUNT(*) FROM awe_conversations").fetchone()[0]
    assert ncols == 2, ncols
    assert delete_run(c, r["id"]) >= 1
    assert len(list_runs(c)) == 0
    print("AVAYA_DB_SMOKE_OK")
