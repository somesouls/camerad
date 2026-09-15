"""
avaya/verify.py — PR2: Verifikasi kelengkapan penarikan + laporan transkrip kosong.

Modul terpisah supaya TIDAK mengubah avaya/db.py (aman untuk fitur existing).

Menyimpan baseline jumlah baris RAW per hari (dari Avaya, pre-dedup) di tabel
awe_stage_baseline, lalu membandingkannya dengan hasil hitung ulang (fresh)
dan/atau jumlah baris unik yang sudah tersimpan (staged, informasional).
"""

from datetime import datetime, timedelta, timezone

_JKT = timezone(timedelta(hours=7))


def _now_iso():
    return datetime.now(_JKT).strftime("%Y-%m-%d %H:%M:%S")


def _days_in_range(day_from, day_to):
    """Inclusive list of YYYY-MM-DD strings."""
    d0 = datetime.strptime(str(day_from)[:10], "%Y-%m-%d").date()
    d1 = datetime.strptime(str(day_to)[:10], "%Y-%m-%d").date()
    if d1 < d0:
        d0, d1 = d1, d0
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def ensure_baseline_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS awe_stage_baseline (
            day        TEXT PRIMARY KEY,
            avaya_rows INTEGER,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def record_baseline(conn, raw_by_day):
    """Upsert baseline jumlah baris RAW per hari.
    raw_by_day: {"YYYY-MM-DD": int}. Nilai None dilewati.
    """
    if not raw_by_day:
        return 0
    ensure_baseline_table(conn)
    now = _now_iso()
    n = 0
    for day, cnt in raw_by_day.items():
        if day is None or cnt is None:
            continue
        d = str(day)[:10]
        conn.execute(
            """
            INSERT INTO awe_stage_baseline (day, avaya_rows, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                avaya_rows = excluded.avaya_rows,
                updated_at = excluded.updated_at
            """,
            (d, int(cnt), now),
        )
        n += 1
    conn.commit()
    return n


def _staged_counts(conn, days):
    """{day: count} baris unik tersimpan di awe_staging per tanggal."""
    if not days:
        return {}
    ph = ",".join("?" for _ in days)
    rows = conn.execute(
        "SELECT substr(tanggal,1,10) AS d, COUNT(*) AS c "
        "FROM awe_staging WHERE substr(tanggal,1,10) IN (" + ph + ") GROUP BY d",
        list(days),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _baseline_counts(conn, days):
    ensure_baseline_table(conn)
    if not days:
        return {}
    ph = ",".join("?" for _ in days)
    rows = conn.execute(
        "SELECT day, avaya_rows FROM awe_stage_baseline WHERE day IN (" + ph + ")",
        list(days),
    ).fetchall()
    return {r[0]: (int(r[1]) if r[1] is not None else None) for r in rows}


def stage_verify_range(conn, day_from, day_to, fresh_counts=None):
    """Verifikasi kelengkapan penarikan untuk rentang [day_from, day_to].

    Membandingkan jumlah baris RAW:
      - baseline  = awe_stage_baseline.avaya_rows (jumlah saat ditarik)
      - avaya_now = fresh_counts[day] (hitung ulang sekarang, opsional)
      - staged    = jumlah baris unik tersimpan (informasional)

    Status per hari:
      belum_ditarik    : tidak ada baseline & tidak ada staged
      tanpa_baseline   : ada staged tapi baseline tidak tercatat
      tak_terverifikasi: ada baseline tapi fresh tidak diberikan
      lengkap          : avaya_now == baseline
      kurang           : avaya_now  > baseline  (ada data baru → perlu tarik ulang)
      berkurang        : avaya_now  < baseline
    """
    days = _days_in_range(day_from, day_to)
    base = _baseline_counts(conn, days)
    staged = _staged_counts(conn, days)
    fresh = fresh_counts or {}

    out_days = []
    lengkap = 0
    kurang = 0
    perlu = []
    for d in days:
        b = base.get(d)
        s = staged.get(d)
        av = fresh.get(d)
        if av is not None:
            av = int(av)
        if b is None and not s:
            status = "belum_ditarik"
            perlu.append(d)
        elif b is None:
            status = "tanpa_baseline"
        elif av is None:
            status = "tak_terverifikasi"
        elif av == b:
            status = "lengkap"
            lengkap += 1
        elif av > b:
            status = "kurang"
            kurang += 1
            perlu.append(d)
        else:
            status = "berkurang"
        out_days.append({
            "day": d,
            "status": status,
            "baseline": b,
            "staged_conv": (s or 0),
            "avaya_now": av,
        })
    fully = (len(perlu) == 0) and all(x["status"] == "lengkap" for x in out_days)
    return {
        "days": out_days,
        "lengkap": lengkap,
        "kurang": kurang,
        "perlu_tarik_ulang": perlu,
        "fully_complete": fully,
    }


def empty_transcript_stats(conn, day_from=None, day_to=None):
    """Laporan transkrip kosong di awe_conversations.

    Kosong = transkrip_json IS NULL OR '' OR '[]'.
    Filter tanggal opsional (inklusif) via substr(tanggal,1,10).
    """
    where = ["1=1"]
    params = []
    if day_from:
        where.append("substr(tanggal,1,10) >= ?")
        params.append(str(day_from)[:10])
    if day_to:
        where.append("substr(tanggal,1,10) <= ?")
        params.append(str(day_to)[:10])
    wsql = " AND ".join(where)

    empty_expr = (
        "(transkrip_json IS NULL OR transkrip_json = '' "
        "OR transkrip_json = '[]')"
    )
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN " + empty_expr + " THEN 1 ELSE 0 END) "
        "FROM awe_conversations WHERE " + wsql,
        params,
    ).fetchone()
    total = int(row[0] or 0)
    empty = int(row[1] or 0)
    filled = total - empty

    by_rows = conn.execute(
        "SELECT substr(tanggal,1,10) AS d, COUNT(*) AS t, "
        "SUM(CASE WHEN " + empty_expr + " THEN 1 ELSE 0 END) AS e "
        "FROM awe_conversations WHERE " + wsql + " GROUP BY d ORDER BY d",
        params,
    ).fetchall()
    by_day = []
    for r in by_rows:
        e = int(r[2] or 0)
        if e > 0:
            by_day.append({"day": r[0], "total": int(r[1] or 0), "empty": e})
    return {
        "total": total,
        "empty": empty,
        "filled": filled,
        "by_day": by_day,
    }


if __name__ == "__main__":
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE awe_staging (sid TEXT PRIMARY KEY, tanggal TEXT);
        CREATE TABLE awe_stage_coverage (day TEXT PRIMARY KEY, total_conv INTEGER);
        CREATE TABLE awe_conversations (
            run_id TEXT, sid TEXT, tanggal TEXT, transkrip_json TEXT,
            PRIMARY KEY (run_id, sid)
        );
        """
    )
    record_baseline(conn, {"2026-07-02": 2})
    conn.executemany(
        "INSERT INTO awe_staging (sid, tanggal) VALUES (?, ?)",
        [("s1", "2026-07-02"), ("s2", "2026-07-02")],
    )
    conn.commit()

    r = stage_verify_range(conn, "2026-07-02", "2026-07-02")
    d = r["days"][0]
    assert d["status"] == "tak_terverifikasi", d
    assert d["baseline"] == 2 and d["staged_conv"] == 2, d

    r = stage_verify_range(conn, "2026-07-02", "2026-07-02", {"2026-07-02": 2})
    assert r["days"][0]["status"] == "lengkap", r
    assert r["fully_complete"] is True, r

    r = stage_verify_range(conn, "2026-07-02", "2026-07-02", {"2026-07-02": 5})
    assert r["days"][0]["status"] == "kurang", r
    assert r["perlu_tarik_ulang"] == ["2026-07-02"], r
    assert r["fully_complete"] is False, r

    r = stage_verify_range(conn, "2026-07-03", "2026-07-03")
    assert r["days"][0]["status"] == "belum_ditarik", r
    assert r["perlu_tarik_ulang"] == ["2026-07-03"], r

    conn.executemany(
        "INSERT INTO awe_conversations (run_id, sid, tanggal, transkrip_json) "
        "VALUES (?, ?, ?, ?)",
        [
            ("r1", "a1", "2026-07-02", '[{"t":"hi"}]'),
            ("r1", "a2", "2026-07-02", ""),
            ("r1", "b1", "2026-07-03", None),
        ],
    )
    conn.commit()
    g = empty_transcript_stats(conn)
    assert g["total"] == 3 and g["empty"] == 2 and g["filled"] == 1, g
    days_e = {x["day"]: x["empty"] for x in g["by_day"]}
    assert days_e == {"2026-07-02": 1, "2026-07-03": 1}, g

    print("AVAYA_VERIFY_SMOKE_OK")
