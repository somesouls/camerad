# -*- coding: utf-8 -*-
"""avaya/phone_query.py - baca/tampil interaksi Telepon (lihat phone_db.py)."""
import json as _json

try:
    from .phone_db import init_phone_db
except Exception:
    from phone_db import init_phone_db

_LIST_COLS = ("sid,day,tanggal,ani,dnis,call_id,durasi,hold_time_sec,has_audio,"
              "has_screen,audio_ref,customer,agent_name,transkrip_source,"
              "ringkasan,topik,jenis_layanan,sentiment,emotion,resolusi,frustrasi")


def list_phone(conn, day_from=None, day_to=None, limit=200):
    init_phone_db(conn)
    sql = ("SELECT " + _LIST_COLS +
           ", (transkrip_json IS NOT NULL) AS has_transkrip"
           ", (analisis_json IS NOT NULL) AS has_analisis"
           " FROM awe_phone_interactions WHERE 1=1")
    p = []
    if day_from:
        sql += " AND day>=?"
        p.append(str(day_from)[:10])
    if day_to:
        sql += " AND day<=?"
        p.append(str(day_to)[:10])
    sql += " ORDER BY tanggal DESC, sid DESC LIMIT ?"
    p.append(int(limit))
    rows = conn.execute(sql, p).fetchall()
    return {"interactions": [dict(r) for r in rows], "total": len(rows)}


def get_phone_interaction(conn, sid):
    init_phone_db(conn)
    r = conn.execute("SELECT * FROM awe_phone_interactions WHERE sid=?",
                     (str(sid or "").strip(),)).fetchone()
    if not r:
        return None
    d = dict(r)
    for src, dst in (("transkrip_json", "transkrip"), ("entitas_json", "entitas"),
                     ("poin_json", "poin_penting"), ("analisis_json", "analisis")):
        v = d.pop(src, None)
        if v:
            try:
                d[dst] = _json.loads(v)
            except Exception:
                d[dst] = None
    return d


def phone_coverage(conn, day_from=None, day_to=None):
    init_phone_db(conn)
    sql = ("SELECT day, COUNT(*) AS n_total,"
           " SUM(CASE WHEN has_audio=1 THEN 1 ELSE 0 END) AS n_audio,"
           " SUM(CASE WHEN transkrip_json IS NOT NULL THEN 1 ELSE 0 END) AS n_transkrip,"
           " SUM(CASE WHEN analisis_json IS NOT NULL THEN 1 ELSE 0 END) AS n_analisis"
           " FROM awe_phone_interactions WHERE 1=1")
    p = []
    if day_from:
        sql += " AND day>=?"
        p.append(str(day_from)[:10])
    if day_to:
        sql += " AND day<=?"
        p.append(str(day_to)[:10])
    sql += " GROUP BY day ORDER BY day DESC"
    return [dict(r) for r in conn.execute(sql, p).fetchall()]


def phone_stats(conn):
    init_phone_db(conn)
    r = conn.execute(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN transkrip_json IS NOT NULL THEN 1 ELSE 0 END) AS n_tx,"
        " SUM(CASE WHEN analisis_json IS NOT NULL THEN 1 ELSE 0 END) AS n_an,"
        " MIN(day) AS dmin, MAX(day) AS dmax FROM awe_phone_interactions").fetchone()
    return {"total": r["n"] or 0, "transkrip": r["n_tx"] or 0,
            "analisis": r["n_an"] or 0, "date_min": r["dmin"] or "",
            "date_max": r["dmax"] or ""}


def delete_phone_day(conn, day):
    init_phone_db(conn)
    cur = conn.cursor()
    d = str(day)[:10]
    n = cur.execute("SELECT COUNT(*) FROM awe_phone_interactions WHERE day=?", (d,)).fetchone()[0]
    cur.execute("DELETE FROM awe_phone_interactions WHERE day=?", (d,))
    conn.commit()
    return n
