# -*- coding: utf-8 -*-
"""awe_analytics.py — Analitik AWE (Avaya) in-app + halaman submenu.

Dipasang via studio_routes.register(...) (yang dipanggil dari web_app.py) supaya
TIDAK perlu menyentuh web_app.py (file besar). Menyediakan:
  - GET /api/awe/analytics?range=&start=&end=   (agregasi awe_conversations)
  - 5 halaman submenu AWE: /awe/dasbor, /awe/coverage, /awe/taksonomi,
    /awe/sentimen, /awe/percakapan

Agregasi dilakukan in-app (Python) atas tabel awe_conversations dengan filter
rentang tanggal, sehingga analis TIDAK perlu tarik/olah ulang. Nilai turunan
(deflection gap, reached-agent, topik) diambil dari kolom yang ADA bila kolom
khusus belum tersedia, sehingga langsung jalan atas seluruh data historis.
"""
import datetime as _dt
from collections import Counter, defaultdict

import avaya.db as avdb
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


def _jkt_today():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).date()


def resolve_range(preset, start=None, end=None):
    """Kembalikan (start,end) 'YYYY-MM-DD'. (None,None) = tanpa batas (semua)."""
    preset = (preset or "7d").strip().lower()
    today = _jkt_today()
    iso = lambda d: d.isoformat()
    if preset == "custom":
        return ((start or "")[:10] or None, (end or "")[:10] or None)
    if preset in ("all", "semua", "*"):
        return (None, None)
    if preset in ("today", "hari-ini", "hari_ini"):
        return (iso(today), iso(today))
    if preset in ("yesterday", "kemarin"):
        y = today - _dt.timedelta(days=1)
        return (iso(y), iso(y))
    days = {"7d": 7, "30d": 30, "90d": 90}.get(preset, 7)
    return (iso(today - _dt.timedelta(days=days - 1)), iso(today))


def data_bounds(conn):
    row = conn.execute(
        "SELECT MIN(substr(tanggal,1,10)) AS a, MAX(substr(tanggal,1,10)) AS b "
        "FROM awe_conversations"
    ).fetchone()
    return {"min": (row["a"] or ""), "max": (row["b"] or "")}


def _norm_sent(s):
    s = (s or "").strip().lower()
    if s.startswith("pos"):
        return "Positif"
    if s.startswith("neg"):
        return "Negatif"
    if s.startswith("net") or s.startswith("neu"):
        return "Netral"
    return (s.capitalize() if s else "Tidak diketahui")


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "ya", "yes", "y")


def analytics(conn, start=None, end=None, limit_conv=500):
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?"); params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?"); params.append(end[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT * FROM awe_conversations" + wsql + " ORDER BY tanggal", params
    ).fetchall()

    total = len(rows)
    cov = Counter(); defl = Counter(); beh = Counter(); freq = Counter()
    sent = Counter(); emo = Counter(); tax = Counter()
    defl_topic = defaultdict(lambda: [0, 0])  # topik -> [gap, total]
    agents = {}
    trend = defaultdict(lambda: {"total": 0, "gap": 0, "negatif": 0})
    custset = set()
    conv_out = []
    dmin = dmax = ""

    for r in rows:
        d = dict(r)
        day = (d.get("tanggal") or "")[:10]
        if day:
            dmin = day if not dmin else min(dmin, day)
            dmax = day if not dmax else max(dmax, day)

        band = (d.get("coverage_band") or "").strip().lower()
        if band in ("covered", "tinggi", "high"):
            cov["covered"] += 1
        elif band in ("gray", "grey", "abu", "sedang", "medium"):
            cov["gray"] += 1
        elif band:
            cov["uncovered"] += 1
        else:
            cov["unknown"] += 1

        behavior = (d.get("behavior") or "").strip().lower()
        is_direct = behavior in ("direct", "langsung")
        beh["direct" if is_direct else ("tried_bot" if behavior else "unknown")] += 1

        gap_val = d.get("deflection_gap")
        if gap_val in (None, ""):
            gap = 1 if is_direct else 0
        else:
            gap = 1 if _truthy(gap_val) else 0
        defl["gap" if gap else "no_gap"] += 1

        topik = (str(d.get("topik")).strip() if d.get("topik") else "") \
            or (d.get("mapped_intent") or "").strip() or "(tanpa topik)"
        defl_topic[topik][1] += 1
        if gap:
            defl_topic[topik][0] += 1

        if _truthy(d.get("is_returning")):
            freq["returning"] += 1
        else:
            freq["baru"] += 1

        s1 = _norm_sent(d.get("sentiment"))
        sent[s1] += 1
        em = (d.get("emotion") or "").strip() or "Tidak diketahui"
        emo[em] += 1

        case = (d.get("case_label") or "").strip() or "(tak berlabel)"
        tax[case] += 1

        cust = (d.get("customer") or "").strip()
        if cust:
            custset.add(cust)

        an = (d.get("agent_name") or "").strip() or "(tanpa agent)"
        a = agents.setdefault(an, {"name": an, "count": 0, "durasi_sum": 0, "pos": 0, "neg": 0})
        a["count"] += 1
        try:
            a["durasi_sum"] += int(d.get("durasi") or 0)
        except Exception:
            pass
        if s1 == "Positif":
            a["pos"] += 1
        elif s1 == "Negatif":
            a["neg"] += 1

        t = trend[day]
        t["total"] += 1
        if gap:
            t["gap"] += 1
        if s1 == "Negatif":
            t["negatif"] += 1

        if len(conv_out) < limit_conv:
            try:
                dur = int(d.get("durasi") or 0)
            except Exception:
                dur = 0
            conv_out.append({
                "tanggal": d.get("tanggal") or "", "sid": d.get("sid") or "",
                "customer": cust, "agent_name": an, "durasi": dur,
                "behavior": behavior, "is_returning": _truthy(d.get("is_returning")),
                "mapped_intent": d.get("mapped_intent") or "",
                "coverage_band": d.get("coverage_band") or "",
                "case_label": d.get("case_label") or "", "sentiment": s1,
                "emotion": em, "topik": topik, "deflection_gap": bool(gap),
            })

    agent_list = []
    for a in agents.values():
        cnt = a["count"] or 1
        pos_rate = a["pos"] / cnt
        neg_rate = a["neg"] / cnt
        agent_list.append({
            "name": a["name"], "count": a["count"],
            "avg_durasi": round(a["durasi_sum"] / cnt, 1),
            "pos_rate": round(pos_rate * 100, 1),
            "neg_rate": round(neg_rate * 100, 1),
            "score": round(100 * pos_rate - 50 * neg_rate, 1),
        })
    agent_list.sort(key=lambda x: (-x["score"], -x["count"]))
    for i, a in enumerate(agent_list, 1):
        a["rank"] = i

    dtl = [{"topik": k, "gap": v[0], "total": v[1],
            "gap_pct": round(100 * v[0] / v[1], 1) if v[1] else 0}
           for k, v in defl_topic.items()]
    dtl.sort(key=lambda x: (-x["gap"], -x["total"]))

    trend_list = [dict(day=k, **v) for k, v in sorted(trend.items()) if k]
    cov_total = cov["covered"] + cov["gray"] + cov["uncovered"] + cov["unknown"]

    return {
        "ok": True,
        "range": {"start": start or "", "end": end or ""},
        "meta": {"total": total, "customers": len(custset),
                 "date_min": dmin, "date_max": dmax},
        "coverage": {"covered": cov["covered"], "gray": cov["gray"],
                     "uncovered": cov["uncovered"], "unknown": cov["unknown"],
                     "total": cov_total},
        "deflection": {"gap": defl["gap"], "no_gap": defl["no_gap"],
                       "gap_pct": round(100 * defl["gap"] / total, 1) if total else 0},
        "deflection_by_topic": dtl[:15],
        "behavior": {"direct": beh["direct"], "tried_bot": beh["tried_bot"],
                     "unknown": beh["unknown"]},
        "frequency": {"returning": freq["returning"], "baru": freq["baru"]},
        "taxonomy": [{"label": k, "value": v} for k, v in tax.most_common()],
        "sentiment": {"Positif": sent["Positif"], "Netral": sent["Netral"],
                      "Negatif": sent["Negatif"]},
        "emotions": [{"label": k, "value": v} for k, v in emo.most_common(12)],
        "agents": agent_list,
        "trend": trend_list,
        "conversations": conv_out,
    }


_PAGES = [
    ("/awe/dasbor", "awe_dasbor", "Dashboard AWE", "dasbor"),
    ("/awe/coverage", "awe_coverage", "Coverage & Deflection", "coverage"),
    ("/awe/taksonomi", "awe_taksonomi", "Taksonomi & Peluang", "taksonomi"),
    ("/awe/sentimen", "awe_sentimen", "Sentimen & Agent", "sentimen"),
    ("/awe/percakapan", "awe_percakapan", "Detail Percakapan", "percakapan"),
]


def register(app, *, render_page):
    """Pasang 5 halaman submenu + API analitik ke FastAPI app."""
    def _mk(active, title, section):
        async def _page(request: Request):
            return render_page(request, "awe_analytics.html", active,
                               {"awe_title": title, "awe_section": section})
        return _page

    for path, active, title, section in _PAGES:
        app.add_api_route(path, _mk(active, title, section), methods=["GET"])

    async def api_awe_analytics(request: Request):
        q = request.query_params
        preset = q.get("range") or "7d"
        start = q.get("start"); end = q.get("end")

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                s, e = resolve_range(preset, start, end)
                data = analytics(conn, s, e)
                data["bounds"] = data_bounds(conn)
                data["preset"] = preset
                return data
            finally:
                conn.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

    app.add_api_route("/api/awe/analytics", api_awe_analytics, methods=["GET"])

    # Pasang modul Penilaian QA (Assessor): API transkrip percakapan.
    # Dilakukan di sini agar tidak perlu menyentuh web_app.py maupun
    # studio_routes.py. Halaman /awe/penilaian tetap dirender web_app.py
    # (placeholder.html -> awe_penilaian.html) dan sudah ter-gate ke area
    # "assess" oleh middleware.
    try:
        import awe.assess as awe_assess
        awe_assess.register(app)
    except Exception:
        import traceback
        traceback.print_exc()

    # Pasang modul Analisis Pengguna Harian (daily users) — sub-menu baru AWE.
    # Halaman /awe/pengguna-harian + API /api/awe/daily-users. render_page
    # sudah tersedia di scope ini, sehingga wiring cukup di sini.
    try:
        import awe.daily_users as awe_daily_users
        awe_daily_users.register(app, render_page=render_page)
    except Exception:
        import traceback
        traceback.print_exc()
