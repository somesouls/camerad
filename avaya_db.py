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
            topik         TEXT,
            deflection_gap INTEGER,
            PRIMARY KEY (run_id, sid)
        );
        CREATE INDEX IF NOT EXISTS idx_awe_conv_run ON awe_conversations(run_id);
        CREATE INDEX IF NOT EXISTS idx_awe_conv_intent ON awe_conversations(mapped_intent);
        CREATE TABLE IF NOT EXISTS awe_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_day_coverage (
            day        TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
            run_id     TEXT,
            source     TEXT DEFAULT 'pull',
            total_conv INTEGER,
            pulled_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_awe_cov_run ON awe_day_coverage(run_id);
        CREATE TABLE IF NOT EXISTS awe_staging (
            sid          TEXT PRIMARY KEY,
            tanggal      TEXT,
            agent_id     TEXT,
            agent_name   TEXT,
            customer     TEXT,
            durasi       INTEGER DEFAULT 0,
            payload_json TEXT,
            batch_id     TEXT,
            pulled_by    TEXT,
            pulled_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_awe_staging_tgl ON awe_staging(tanggal);
        CREATE TABLE IF NOT EXISTS awe_stage_batches (
            id         TEXT PRIMARY KEY,
            date_from  TEXT,
            date_to    TEXT,
            n_pulled   INTEGER DEFAULT 0,
            n_new      INTEGER DEFAULT 0,
            pulled_by  TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS awe_stage_coverage (
            day        TEXT PRIMARY KEY,
            batch_id   TEXT,
            total_conv INTEGER,
            pulled_by  TEXT,
            pulled_at  TEXT
        );
        """
    )
    _ensure_columns(cur, "awe_conversations", [
        ("topik", "topik TEXT"),
        ("deflection_gap", "deflection_gap INTEGER"),
    ])
    conn.commit()
    return conn


# ---- util ekstraksi field dashboard (defensif thd variasi nama kunci) ------
def _g(d, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _ensure_columns(cur, table, coldefs):
    """Migrasi ringan: tambah kolom yang belum ada (ALTER TABLE ADD COLUMN).
    Kolom baru nullable -> baris lama bernilai NULL (analitik akan fallback)."""
    have = {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table).fetchall()}
    for name, ddl in coldefs:
        if name not in have:
            cur.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))


def _gap_flag(c):
    """Ambil deflection_gap dari dashboard conv -> 1/0, atau None bila tak ada."""
    v = _g(c, "deflection_gap", "deflectionGap", default=None)
    if v is None:
        return None
    return 1 if str(v).strip().lower() in ("1", "true", "ya", "yes", "y") else 0


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


def _days_in_range(day_from, day_to):
    """List tanggal inklusif YYYY-MM-DD dari day_from s/d day_to."""
    try:
        a = _dt.date.fromisoformat(str(day_from)[:10])
        b = _dt.date.fromisoformat(str(day_to)[:10])
    except Exception:
        return []
    if b < a:
        a, b = b, a
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += _dt.timedelta(days=1)
    return out


def _mark_days(cur, days, run_id, source, total_conv):
    now = _jkt_now_iso()
    for d in days:
        if not d:
            continue
        cur.execute(
            "INSERT INTO awe_day_coverage(day,run_id,source,total_conv,pulled_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET "
            "run_id=excluded.run_id, source=excluded.source, "
            "total_conv=excluded.total_conv, pulled_at=excluded.pulled_at",
            (d, run_id, source, total_conv, now),
        )


def mark_days_covered(conn, day_from, day_to, run_id=None, source="pull", total_conv=None):
    """Tandai rentang hari sebagai sudah ditarik (dipakai flow tarik-langsung)."""
    cur = conn.cursor()
    _mark_days(cur, _days_in_range(day_from, day_to), run_id, source, total_conv)
    conn.commit()
    return _days_in_range(day_from, day_to)


def covered_days(conn):
    cur = conn.cursor()
    rs = cur.execute("SELECT day FROM awe_day_coverage").fetchall()
    return set(r["day"] for r in rs)


def coverage_for_range(conn, day_from, day_to):
    """Bagi rentang jadi hari yang SUDAH ada vs BELUM ada di database AWE.

    Return: {requested, covered, missing, runs} di mana runs = daftar run
    (id,label,...) yang memuat hari-hari tercakup, agar UI bisa langsung buka
    dashboard tanpa tarik ulang.
    """
    req = _days_in_range(day_from, day_to)
    cur = conn.cursor()
    cov_map = {}
    if req:
        qs = ",".join("?" for _ in req)
        rs = cur.execute(
            "SELECT day, run_id FROM awe_day_coverage WHERE day IN (%s)" % qs, req
        ).fetchall()
        for r in rs:
            cov_map[r["day"]] = r["run_id"]
    covered = [d for d in req if d in cov_map]
    missing = [d for d in req if d not in cov_map]
    run_ids = [rid for rid in dict.fromkeys(cov_map.values()) if rid]
    runs = []
    for rid in run_ids:
        rr = get_run(conn, rid)
        if rr:
            runs.append({k: rr[k] for k in ("id", "label", "date_min", "date_max",
                                             "total_conv", "total_cust", "source", "created_at")})
    return {"requested": req, "covered": covered, "missing": missing, "runs": runs}


def save_run(conn, dashboard, records=None, label=None, n_files=0, source="upload", build=None,
             cover_from=None, cover_to=None):
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
            (str(_g(c, "topik", "topic", default="")) or None),
            _gap_flag(c),
        ))
    if rows:
        cur.executemany(
            """INSERT OR REPLACE INTO awe_conversations
                 (run_id,sid,tanggal,customer,agent_name,agent_id,durasi,behavior,
                  is_returning,mapped_intent,coverage_band,case_label,sentiment,emotion,
                  topik,deflection_gap)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    # tandai cakupan hari (agar analis tak perlu tarik ulang tanggal yang sudah ada)
    if cover_from and cover_to:
        _mark_days(cur, _days_in_range(cover_from, cover_to), run_id, source, total_conv)
    else:
        _cdays = sorted({str(_g(c, "tanggal", "date", "start", default=""))[:10]
                         for c in convs if isinstance(c, dict)})
        _mark_days(cur, [d for d in _cdays if d], run_id, source, None)
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


# =============================================================
# STAGING (penyimpanan sementara data mentah AWE) untuk alur
# "Kelola Data AWE" 2-tahap: (1) TARIK ke staging, (2) PROSES ke awe_runs.
# Dedup lintas tarikan & lintas pengguna berdasarkan sid.
# =============================================================
def _stage_day_of(c):
    return str(_g(c, "tanggal", "date", "start", default=""))[:10]


def stage_upsert_convs(conn, convs, batch_id=None, pulled_by=None):
    """Sisipkan percakapan mentah ke staging. Dedup by sid (INSERT OR IGNORE:
    percakapan yang sudah ada TIDAK ditimpa -> aman untuk melengkapi)."""
    cur = conn.cursor()
    now = _jkt_now_iso()
    n_seen = n_new = 0
    for c in convs:
        if not isinstance(c, dict):
            continue
        sid = str(_g(c, "sid", "Sid", default="")).strip()
        if not sid:
            continue
        n_seen += 1
        r = cur.execute(
            "INSERT OR IGNORE INTO awe_staging"
            "(sid,tanggal,agent_id,agent_name,customer,durasi,payload_json,batch_id,pulled_by,pulled_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, _stage_day_of(c),
             str(_g(c, "agentId", "agent_id", default="")),
             str(_g(c, "agentName", "agent", "agent_name", default="")),
             str(_g(c, "customer", "pelanggan", default="")),
             int(_g(c, "durasi", "duration", default=0) or 0),
             _json.dumps(c, ensure_ascii=False),
             batch_id or "", pulled_by or "", now),
        )
        if r.rowcount:
            n_new += 1
    conn.commit()
    return {"seen": n_seen, "new": n_new, "dup": n_seen - n_new}


def stage_add_batch(conn, batch_id, date_from, date_to, n_pulled, n_new, pulled_by=None):
    conn.execute(
        "INSERT OR REPLACE INTO awe_stage_batches"
        "(id,date_from,date_to,n_pulled,n_new,pulled_by,created_at) VALUES(?,?,?,?,?,?,?)",
        (batch_id, str(date_from)[:10], str(date_to)[:10], int(n_pulled or 0),
         int(n_new or 0), pulled_by or "", _jkt_now_iso()),
    )
    conn.commit()


def stage_mark_days(conn, convs, day_from=None, day_to=None, batch_id=None, pulled_by=None):
    """Tandai hari-hari yang KINI ada di staging (pakai tanggal aktual dari
    percakapan; fallback ke rentang bila kosong)."""
    now = _jkt_now_iso()
    by_day = {}
    for c in convs:
        if not isinstance(c, dict):
            continue
        d = _stage_day_of(c)
        if d:
            by_day[d] = by_day.get(d, 0) + 1
    days = sorted(by_day) or [d for d in _days_in_range(day_from, day_to) if d]
    cur = conn.cursor()
    for d in days:
        cur.execute(
            "INSERT INTO awe_stage_coverage(day,batch_id,total_conv,pulled_by,pulled_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET"
            " batch_id=excluded.batch_id, total_conv=excluded.total_conv,"
            " pulled_by=excluded.pulled_by, pulled_at=excluded.pulled_at",
            (d, batch_id or "", by_day.get(d), pulled_by or "", now),
        )
    conn.commit()
    return days


def stage_coverage_for_range(conn, day_from, day_to):
    req = _days_in_range(day_from, day_to)
    cur = conn.cursor()
    have = set()
    if req:
        qs = ",".join("?" for _ in req)
        rs = cur.execute(
            "SELECT day FROM awe_stage_coverage WHERE day IN (%s)" % qs, req
        ).fetchall()
        have = set(r["day"] for r in rs)
    staged = [d for d in req if d in have]
    missing = [d for d in req if d not in have]
    return {"requested": req, "staged": staged, "missing": missing}


def stage_stats(conn):
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COUNT(*) AS n, MIN(tanggal) AS dmin, MAX(tanggal) AS dmax FROM awe_staging"
    ).fetchone()
    ndays = cur.execute("SELECT COUNT(*) FROM awe_stage_coverage").fetchone()[0]
    nb = cur.execute("SELECT COUNT(*) FROM awe_stage_batches").fetchone()[0]
    return {"total": row["n"] or 0, "date_min": row["dmin"] or "",
            "date_max": row["dmax"] or "", "days": ndays, "batches": nb}


def stage_list_batches(conn, limit=100):
    rs = conn.execute(
        "SELECT id,date_from,date_to,n_pulled,n_new,pulled_by,created_at"
        " FROM awe_stage_batches ORDER BY datetime(created_at) DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rs]


def stage_count(conn, day_from=None, day_to=None):
    if day_from and day_to:
        return conn.execute(
            "SELECT COUNT(*) FROM awe_staging WHERE tanggal>=? AND tanggal<=?",
            (str(day_from)[:10], str(day_to)[:10]),
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]


def stage_load_convs(conn, day_from=None, day_to=None):
    if day_from and day_to:
        rs = conn.execute(
            "SELECT payload_json FROM awe_staging WHERE tanggal>=? AND tanggal<=?"
            " ORDER BY tanggal, sid", (str(day_from)[:10], str(day_to)[:10]),
        ).fetchall()
    else:
        rs = conn.execute(
            "SELECT payload_json FROM awe_staging ORDER BY tanggal, sid"
        ).fetchall()
    out = []
    for r in rs:
        try:
            out.append(_json.loads(r["payload_json"]))
        except Exception:
            pass
    return out


def stage_purge(conn, day_from=None, day_to=None):
    cur = conn.cursor()
    if day_from and day_to:
        a, b = str(day_from)[:10], str(day_to)[:10]
        n = cur.execute("SELECT COUNT(*) FROM awe_staging WHERE tanggal>=? AND tanggal<=?", (a, b)).fetchone()[0]
        cur.execute("DELETE FROM awe_staging WHERE tanggal>=? AND tanggal<=?", (a, b))
        cur.execute("DELETE FROM awe_stage_coverage WHERE day>=? AND day<=?", (a, b))
        cur.execute("DELETE FROM awe_stage_batches WHERE date_from>=? AND date_to<=?", (a, b))
    else:
        n = cur.execute("SELECT COUNT(*) FROM awe_staging").fetchone()[0]
        cur.execute("DELETE FROM awe_staging")
        cur.execute("DELETE FROM awe_stage_coverage")
        cur.execute("DELETE FROM awe_stage_batches")
    conn.commit()
    return n


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
