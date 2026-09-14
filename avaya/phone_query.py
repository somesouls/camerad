# -*- coding: utf-8 -*-
"""avaya/phone_query.py - baca/tampil interaksi Telepon (lihat phone_db.py)."""
import json as _json

try:
    from .phone_db import init_phone_db
except Exception:
    from phone_db import init_phone_db

# Pencarian isi percakapan bersama (keyword + gmail dot-trick). Fail-soft: bila
# modul tak tersedia, pencarian isi dilewati & daftar tetap jalan seperti biasa.
try:
    import awe.content_search as csearch
except Exception:
    try:
        import content_search as csearch
    except Exception:
        csearch = None

_GMAIL_MODES = ("gmail_dot", "gmail-dot", "email_dot", "calo")
_MAX_SCAN = 20000  # batas baris yang dipindai isi-nya agar aman untuk web request

_LIST_COLS = ("sid,day,tanggal,ani,dnis,call_id,durasi,hold_time_sec,has_audio,"
              "has_screen,audio_ref,customer,agent_name,transkrip_source,"
              "ringkasan,topik,jenis_layanan,sentiment,emotion,resolusi,frustrasi")


def _list_where(day_from=None, day_to=None, agent=None, sentiment=None,
                resolusi=None, frustrasi=None, status=None,
                sid=None, ani=None, customer=None):
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
    # Pencarian teks bebas (tidak peka huruf, sebagian) untuk SID / nomor / nama.
    if sid:
        where.append("lower(coalesce(sid,'')) LIKE ?")
        p.append("%" + str(sid).strip().lower() + "%")
    if ani:
        where.append("lower(coalesce(ani,'')) LIKE ?")
        p.append("%" + str(ani).strip().lower() + "%")
    if customer:
        where.append("lower(coalesce(customer,'')) LIKE ?")
        p.append("%" + str(customer).strip().lower() + "%")
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
               with_options=False, sid=None, ani=None, customer=None,
               content=None, mode="keyword"):
    """Daftar interaksi telepon dengan pagination + filter sisi-server.

    Kembalikan {interactions, total, offset, limit, options?}. `total` = jumlah
    baris yang cocok filter (bukan hanya halaman ini) supaya pager akurat.

    Bila `content` diisi, dijalankan pencarian ISI percakapan (memindai
    transkrip + teks STT) menurut `mode`:
      - "keyword"   : semua kata/frasa harus muncul.
      - "gmail_dot" : email Gmail trik-titik (calo) - setiap baris cocok juga
                      menyertakan daftar `emails` yang terdeteksi.
      - "email"     : email apa pun.
    Pencarian isi memindai SELURUH baris yang lolos filter (dibatasi _MAX_SCAN)
    lalu dipotong per halaman, sehingga hasil menjangkau semua data.
    """
    init_phone_db(conn)
    wsql, p = _list_where(day_from, day_to, agent, sentiment, resolusi,
                          frustrasi, status, sid=sid, ani=ani, customer=customer)
    off = max(int(offset or 0), 0)
    lim = max(int(limit or 25), 1)

    content = str(content or "").strip()
    if content and csearch is not None:
        # ---- Jalur pencarian ISI percakapan (server-side) ----
        gmail_mode = str(mode or "").strip().lower() in _GMAIL_MODES
        scan_where = wsql
        scan_params = list(p)
        if gmail_mode:
            # Pra-saring agar baris yang dipindai lebih sedikit (butuh 'gmail').
            scan_where += (" AND lower(coalesce(transkrip_json,'')"
                           " || ' ' || coalesce(stt_text,'')) LIKE ?")
            scan_params.append("%gmail%")
        sql = ("SELECT " + _LIST_COLS +
               ", transkrip_json, stt_text"
               ", (transkrip_json IS NOT NULL) AS has_transkrip"
               ", (analisis_json IS NOT NULL) AS has_analisis"
               " FROM awe_phone_interactions" + scan_where +
               " ORDER BY tanggal DESC, sid DESC")
        rows = conn.execute(sql, scan_params).fetchall()
        matched = []
        scanned = 0
        scan_capped = False
        for r in rows:
            if scanned >= _MAX_SCAN:
                scan_capped = True
                break
            scanned += 1
            d = dict(r)
            txt = csearch.transcript_text(d.get("transkrip_json") or "")
            stt = d.get("stt_text")
            if stt:
                txt = (txt + "\n" + str(stt)) if txt else str(stt)
            if not csearch.text_matches(txt, content, mode):
                continue
            if gmail_mode:
                d["emails"] = csearch.find_emails(txt, dot_trick_only=True)
            elif csearch.is_specific_mode(mode):
                d["emails"] = csearch.find_emails(txt)
            d.pop("transkrip_json", None)
            d.pop("stt_text", None)
            matched.append(d)
        total = len(matched)
        out = {"interactions": matched[off:off + lim], "total": total,
               "offset": off, "limit": lim, "scanned": scanned,
               "scan_capped": scan_capped}
        if with_options:
            out["options"] = _list_options(conn, day_from, day_to)
        return out

    # ---- Jalur cepat (tanpa pencarian isi) ----
    total = conn.execute(
        "SELECT COUNT(*) FROM awe_phone_interactions" + wsql, p).fetchone()[0]
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
