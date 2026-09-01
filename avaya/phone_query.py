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


def _list_where(day_from=None, day_to=None, agent=None, sentiment=None,
                resolusi=None, frustrasi=None, status=None):
    """Bangun klausa WHERE + params untuk daftar interaksi telepon."""
    where = ["1=1"]
    p = []
    if day_from:
        where.append("day>=?")
        p.append(str(day_from)[:10])
    if day_to:
        where.append("day<=?")
        p.append(str(day_to)[:10])
    if agent:
        where.append("agent_name=?")
        p.append(str(agent))
    if sentiment:
        where.append("sentiment=?")
        p.append(str(sentiment))
    if resolusi:
        where.append("resolusi=?")
        p.append(str(resolusi))
    fr = str(frustrasi or "").strip().lower()
    if fr in ("ya", "yes", "true", "1", "y"):
        where.append("lower(coalesce(frustrasi,'')) in ('1','true','ya','yes','y')")
    elif fr in ("tidak", "no", "false", "0", "n"):
        where.append("lower(coalesce(frustrasi,'')) not in ('1','true','ya','yes','y')")
    st = str(status or "").strip().lower()
    if st in ("analisis", "sudah", "dianalisis"):
        where.append("analisis_json IS NOT NULL AND analisis_json<>''")
    elif st in ("transkrip", "transkrip_saja"):
        where.append("transkrip_json IS NOT NULL AND (analisis_json IS NULL OR analisis_json='')")
    elif st in ("belum", "none", "kosong"):
        where.append("transkrip_json IS NULL AND (analisis_json IS NULL OR analisis_json='')")
    return " WHERE " + " AND ".join(where), p


def _list_options(conn, day_from=None, day_to=None):
    """Nilai distinct untuk dropdown filter (dibatasi rentang tanggal saja)."""
    wsql, p = _list_where(day_from, day_to)

    def _distinct(col):
        rows = conn.execute(
            "SELECT DISTINCT " + col + " AS v FROM awe_phone_interactions" +
            wsql + " AND " + col + " IS NOT NULL AND " + col + "<>'' ORDER BY v",
            p).fetchall()
        return [r["v"] for r in rows]

    return {"agents": _distinct("agent_name"),
            "sentiments": _distinct("sentiment"),
            "resolutions": _distinct("resolusi")}


def list_phone(conn, day_from=None, day_to=None, limit=25, offset=0, agent=None,
               sentiment=None, resolusi=None, frustrasi=None, status=None,
               with_options=False):
    """Daftar interaksi telepon dengan pagination + filter sisi-server.

    Kembalikan {interactions, total, offset, limit, options?}. `total` = jumlah
    baris yang cocok filter (bukan hanya halaman ini) supaya pager akurat.
    """
    init_phone_db(conn)
    wsql, p = _list_where(day_from, day_to, agent, sentiment, resolusi,
                          frustrasi, status)
    total = conn.execute(
        "SELECT COUNT(*) FROM awe_phone_interactions" + wsql, p).fetchone()[0]
    off = max(int(offset or 0), 0)
    lim = max(int(limit or 25), 1)
    sql = ("SELECT " + _LIST_COLS +
           ", (transkrip_json IS NOT NULL) AS has_transkrip"
           ", (analisis_json IS NOT NULL) AS has_analisis"
           " FROM awe_phone_interactions" + wsql +
           " ORDER BY tanggal DESC, sid DESC LIMIT ? OFFSET ?")
    rows = conn.execute(sql, p + [lim, off]).fetchall()
    out = {"interactions": [dict(r) for r in rows], "total": int(total or 0),
           "offset": off, "limit": lim}
    if with_options:
        out["options"] = _list_options(conn, day_from, day_to)
    return out


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
