"""AWE Ikhtisar (§13.3) — KPI ringkas eksekutif atas data awe_conversations.

Menyediakan:
  - overview(conn, start, end, channel) -> dict KPI
  - register(app, *, render_page) -> pasang halaman /awe/ikhtisar + API /api/awe/ikhtisar

Desain: berdiri sendiri dari analitik lain; hanya baca tabel awe_conversations.
Range preset & batas tanggal di-reuse dari awe_analytics agar konsisten dengan
sub-menu AWE lainnya. Jika awe_analytics tak tersedia, pakai resolver lokal.
"""

from collections import Counter

try:
    import avaya_db as avdb
except Exception:  # pragma: no cover - hanya untuk lingkungan uji terisolasi
    avdb = None


def _pct(n, d):
    return round(1000.0 * n / d) / 10.0 if d else 0.0


def _truthy(v):
    if v in (1, True):
        return True
    s = str(v or "").strip().lower()
    return s in ("1", "true", "ya", "yes", "y", "__yes__")


def _norm_sent(v):
    s = str(v or "").strip().lower()
    if s.startswith("pos") or s in ("positif", "positive"):
        return "Positif"
    if s.startswith("neg") or s in ("negatif", "negative"):
        return "Negatif"
    return "Netral"


def _fmt_dur(sec):
    try:
        sec = int(round(float(sec)))
    except Exception:
        sec = 0
    if sec <= 0:
        return "–"
    m, s = divmod(sec, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return "%dj %dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def overview(conn, start=None, end=None, channel=None):
    """Hitung KPI ikhtisar untuk rentang [start,end] (tanggal ISO, opsional)."""
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?")
        params.append(str(start)[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?")
        params.append(str(end)[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT tanggal, agent_name, durasi, behavior, sentiment, emotion, "
        "topik, mapped_intent, is_returning, jenis_layanan "
        "FROM awe_conversations" + wsql + " ORDER BY tanggal",
        params,
    ).fetchall()

    total = len(rows)
    reached = direct = 0
    dur_sum = dur_n = 0
    dur_r_sum = dur_r_n = 0
    sent = Counter()
    emo = Counter()
    topi = Counter()
    lay = Counter()
    ret = Counter()
    trend = {}
    dmin = dmax = ""

    for r in rows:
        try:
            d = dict(r)
        except Exception:
            # sqlite3.Row biasanya bisa dict(); fallback via keys()
            d = {k: r[k] for k in r.keys()}
        day = (str(d.get("tanggal") or ""))[:10]
        if day:
            dmin = day if not dmin else min(dmin, day)
            dmax = day if not dmax else max(dmax, day)
            t = trend.setdefault(day, {"total": 0, "reached": 0})
            t["total"] += 1
        is_reached = bool((str(d.get("agent_name") or "")).strip())
        if is_reached:
            reached += 1
            if day:
                trend[day]["reached"] += 1
        beh = str(d.get("behavior") or "").strip().lower()
        if beh in ("direct", "langsung"):
            direct += 1
        try:
            du = int(float(d.get("durasi") or 0))
        except Exception:
            du = 0
        if du > 0:
            dur_sum += du
            dur_n += 1
            if is_reached:
                dur_r_sum += du
                dur_r_n += 1
        sent[_norm_sent(d.get("sentiment"))] += 1
        em = str(d.get("emotion") or "").strip() or "Tidak diketahui"
        emo[em] += 1
        tp = (
            str(d.get("topik")).strip() if d.get("topik") else ""
        ) or str(d.get("mapped_intent") or "").strip() or "(tanpa topik)"
        topi[tp] += 1
        jl = str(d.get("jenis_layanan") or "").strip()
        if jl:
            lay[jl] += 1
        ret["returning" if _truthy(d.get("is_returning")) else "baru"] += 1

    avg = round(dur_sum / dur_n) if dur_n else 0
    avg_r = round(dur_r_sum / dur_r_n) if dur_r_n else 0
    trend_list = [
        {"day": k, "total": v["total"], "reached": v["reached"]}
        for k, v in sorted(trend.items())
        if k
    ]

    return {
        "ok": True,
        "channel": (channel or "semua"),
        "range": {"start": start or "", "end": end or ""},
        "meta": {"total": total, "date_min": dmin, "date_max": dmax},
        "kpi": {
            "total": total,
            "reached": reached,
            "reached_pct": _pct(reached, total),
            "self_service": total - reached,
            "self_service_pct": _pct(total - reached, total),
            "direct": direct,
            "direct_pct": _pct(direct, total),
            "avg_handle_sec": avg,
            "avg_handle_fmt": _fmt_dur(avg),
            "avg_handle_reached_sec": avg_r,
            "avg_handle_reached_fmt": _fmt_dur(avg_r),
            "neg_pct": _pct(sent.get("Negatif", 0), total),
            "pos_pct": _pct(sent.get("Positif", 0), total),
        },
        "sentiment": {
            "Positif": sent.get("Positif", 0),
            "Netral": sent.get("Netral", 0),
            "Negatif": sent.get("Negatif", 0),
        },
        "emotions": [{"label": k, "value": v} for k, v in emo.most_common(10)],
        "top_topics": [
            {"topik": k, "count": v, "pct": _pct(v, total)}
            for k, v in topi.most_common(12)
        ],
        "top_layanan": [{"label": k, "value": v} for k, v in lay.most_common(8)],
        "frequency": {
            "returning": ret.get("returning", 0),
            "baru": ret.get("baru", 0),
        },
        "trend": trend_list,
    }


def _resolve_range(preset, start, end):
    """Pakai resolver awe_analytics bila ada; jika tidak, resolver lokal ringan."""
    try:
        import awe_analytics as _an
        return _an.resolve_range(preset, start, end)
    except Exception:
        pass
    import datetime as _dt
    try:
        import pytz
        today = _dt.datetime.now(pytz.timezone("Asia/Jakarta")).date()
    except Exception:
        today = _dt.date.today()
    p = (preset or "30d").lower()
    if p == "custom":
        return (start or None), (end or None)
    if p == "all":
        return None, None
    if p == "today":
        return today.isoformat(), today.isoformat()
    if p == "yesterday":
        y = today - _dt.timedelta(days=1)
        return y.isoformat(), y.isoformat()
    days = {"7d": 7, "30d": 30, "90d": 90}.get(p, 30)
    return (today - _dt.timedelta(days=days - 1)).isoformat(), today.isoformat()


def _bounds(conn):
    try:
        import awe_analytics as _an
        if hasattr(_an, "data_bounds"):
            return _an.data_bounds(conn)
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT MIN(substr(tanggal,1,10)), MAX(substr(tanggal,1,10)) "
            "FROM awe_conversations"
        ).fetchone()
        return {"min": (row[0] if row else None), "max": (row[1] if row else None)}
    except Exception:
        return {"min": None, "max": None}


def register(app, *, render_page):
    """Pasang halaman Ikhtisar AWE + endpoint API. Dipanggil dari
    awe_analytics.register() yang sudah memegang render_page."""
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    async def _page(request):
        return render_page(request, "awe_ikhtisar.html", "awe_ikhtisar")

    async def _api(request):
        q = request.query_params
        preset = q.get("range") or "30d"
        start = q.get("start")
        end = q.get("end")
        channel = q.get("channel")

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                s, e = _resolve_range(preset, start, end)
                data = overview(conn, s, e, channel)
                data["preset"] = preset
                data["bounds"] = _bounds(conn)
                return data
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

    app.add_api_route("/awe/ikhtisar", _page, methods=["GET"])
    app.add_api_route("/api/awe/ikhtisar", _api, methods=["GET"])
