# -*- coding: utf-8 -*-
"""sosmed/pull_log.py — Log penarikan per (platform, hari).

Menjawab isu \"hasil tarikan bisa sampai induk\": keberadaan item pada created_at
suatu hari TIDAK bisa dipakai sebagai penanda \"hari itu sudah ditarik\" (karena
penarikan bisa membawa tweet INDUK dari hari lain, dan sebaliknya hari yang
benar-benar sepi tetap harus ditandai \"sudah ditarik\" walau 0 item). Modul ini
menyimpan penanda EKSPLISIT: satu baris per (platform, hari) begitu penarikan
untuk hari itu dijalankan — terlepas dari jumlah item.

Tabel dibuat lazily (ensure) memakai koneksi sosmed.db yang sama (sqlite).
Hanya STDLIB.
"""
import datetime as _dt


def ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sosmed_pull_log (
            platform TEXT,
            day TEXT,
            status TEXT DEFAULT 'pulled',
            n_new INTEGER DEFAULT 0,
            n_fetched INTEGER DEFAULT 0,
            batch_id TEXT,
            trigger TEXT,
            first_pulled_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (platform, day)
        )
    """)
    conn.commit()
    return conn


def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mark(conn, platform, day, status="pulled", n_new=0, n_fetched=0,
         batch_id="", trigger=""):
    """Tandai (platform, day) sudah ditarik. Idempoten (upsert)."""
    ensure(conn)
    now = _now()
    row = conn.execute(
        "SELECT day FROM sosmed_pull_log WHERE platform=? AND day=?",
        (platform, day)).fetchone()
    if row:
        conn.execute(
            "UPDATE sosmed_pull_log SET status=?, n_new=?, n_fetched=?, "
            "batch_id=?, trigger=?, updated_at=? WHERE platform=? AND day=?",
            (status, int(n_new or 0), int(n_fetched or 0), batch_id or "",
             trigger or "", now, platform, day))
    else:
        conn.execute(
            "INSERT INTO sosmed_pull_log(platform,day,status,n_new,n_fetched,"
            "batch_id,trigger,first_pulled_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (platform, day, status, int(n_new or 0), int(n_fetched or 0),
             batch_id or "", trigger or "", now, now))
    conn.commit()
    return True


def is_pulled(conn, platform, day):
    ensure(conn)
    r = conn.execute(
        "SELECT 1 FROM sosmed_pull_log WHERE platform=? AND day=? "
        "AND status='pulled'", (platform, day)).fetchone()
    return bool(r)


def list_days(conn, platform="x", start="", end="", limit=400):
    ensure(conn)
    where = ["platform=?"]
    params = [platform]
    if start:
        where.append("day>=?"); params.append(start)
    if end:
        where.append("day<=?"); params.append(end)
    sql = ("SELECT platform,day,status,n_new,n_fetched,batch_id,trigger,"
           "first_pulled_at,updated_at FROM sosmed_pull_log WHERE "
           + " AND ".join(where) + " ORDER BY day DESC LIMIT ?")
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


if __name__ == "__main__":
    import os as _os, tempfile as _tf, sqlite3
    dbf = _os.path.join(_tf.mkdtemp(), "pl.db")
    c = sqlite3.connect(dbf)
    c.row_factory = sqlite3.Row
    ensure(c)
    assert is_pulled(c, "x", "2026-09-06") is False
    mark(c, "x", "2026-09-06", n_new=0, n_fetched=0, trigger="test")
    assert is_pulled(c, "x", "2026-09-06") is True
    mark(c, "x", "2026-09-06", n_new=5, n_fetched=12, trigger="test2")
    days = list_days(c, "x")
    assert len(days) == 1 and days[0]["n_new"] == 5 and days[0]["n_fetched"] == 12, days
    c.close()
    print("SOSMED_PULL_LOG_SMOKE_OK")
