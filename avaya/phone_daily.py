# -*- coding: utf-8 -*-
"""avaya/phone_daily.py - Analisis "Pengguna Harian" Telepon (AWE Telepon).

Meniru pola awe/daily_users.py (Livechat) tetapi untuk interaksi TELEPON di
tabel awe_phone_interactions. Kunci identitas = ANI (nomor telepon penelepon),
bukan NIK/NPWP: percakapan telepon tidak selalu menyebut identitas, jadi nomor
telepon dipakai sebagai filter utama untuk melihat siapa yang sering menghubungi
Kring Pajak. "Deteksi tema otomatis" = agregasi jenis_layanan/topik hasil
analisis LLM per penelepon dan secara keseluruhan.

Semua fungsi menerima conn (koneksi avaya.db, row_factory=Row). Baca-saja; tidak
menyentuh alur Chat.
"""
import datetime as _dt
from collections import Counter, defaultdict

try:
    from .phone_db import init_phone_db
except Exception:
    try:
        from phone_db import init_phone_db
    except Exception:
        init_phone_db = None


def _jkt_today():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).date()


def resolve_range(preset, start=None, end=None):
    """Kembalikan (start,end) 'YYYY-MM-DD'. (None,None)=semua. Default 30 hari."""
    preset = (preset or "30d").strip().lower()
    today = _jkt_today()

    def iso(d):
        return d.isoformat()

    if preset == "custom":
        return ((start or "")[:10] or None, (end or "")[:10] or None)
    if preset in ("all", "semua", "*"):
        return (None, None)
    if preset in ("today", "hari-ini", "hari_ini"):
        return (iso(today), iso(today))
    if preset in ("yesterday", "kemarin"):
        y = today - _dt.timedelta(days=1)
        return (iso(y), iso(y))
    days = {"7d": 7, "30d": 30, "90d": 90}.get(preset, 30)
    return (iso(today - _dt.timedelta(days=days - 1)), iso(today))


def data_bounds(conn):
    try:
        row = conn.execute(
            "SELECT MIN(substr(tanggal,1,10)) AS a, MAX(substr(tanggal,1,10)) AS b "
            "FROM awe_phone_interactions").fetchone()
        return {"min": (row["a"] or ""), "max": (row["b"] or "")}
    except Exception:
        return {"min": "", "max": ""}


def _norm_ani(v):
    """Normalisasi nomor telepon: buang semua spasi. '' bila kosong."""
    return "".join(str(v or "").strip().split())


def _norm_sent(s):
    s = (s or "").strip().lower()
    if s.startswith("pos"):
        return "Positif"
    if s.startswith("neg"):
        return "Negatif"
    if s.startswith("net") or s.startswith("neu"):
        return "Netral"
    return "Tidak diketahui"


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "ya", "yes", "y")


def _theme_of(d):
    jl = str(d.get("jenis_layanan") or "").strip()
    if jl:
        return jl
    tp = str(d.get("topik") or "").strip()
    if tp:
        return tp.split(",")[0].strip() or "(tanpa tema)"
    if not (d.get("analisis_json") or d.get("stt_text")):
        return "(belum dianalisis)"
    return "(tanpa tema)"


def _analyzed(d):
    return bool(str(d.get("analisis_json") or "").strip())


def _num(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def compute(conn, start=None, end=None, limit_users=1000):
    """Agregasi pengguna harian telepon per ANI dalam rentang tanggal."""
    if init_phone_db is not None:
        try:
            init_phone_db(conn)
        except Exception:
            pass
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?")
        params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?")
        params.append(end[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT sid,tanggal,day,ani,agent_name,durasi,hold_time_sec,has_audio,"
        "topik,jenis_layanan,sentiment,emotion,resolusi,frustrasi,"
        "transkrip_source,analisis_json,stt_text "
        "FROM awe_phone_interactions" + wsql + " ORDER BY tanggal", params
    ).fetchall()

    callers = {}
    day_callers = defaultdict(set)
    day_calls = Counter()
    all_theme = Counter()
    sent_dist = Counter()
    reso_dist = Counter()
    dmin = dmax = ""
    total_calls = 0
    analyzed_calls = 0
    frustrasi_calls = 0
    anon_calls = 0
    anon_callers = set()
    dur_sum = dur_n = hold_sum = 0

    for r in rows:
        d = dict(r)
        total_calls += 1
        day = (str(d.get("tanggal") or "")[:10]) or (str(d.get("day") or "")[:10])
        if day:
            dmin = day if not dmin else min(dmin, day)
            dmax = day if not dmax else max(dmax, day)
        ani = _norm_ani(d.get("ani"))
        theme = _theme_of(d)
        sent = _norm_sent(d.get("sentiment"))
        reso = str(d.get("resolusi") or "").strip()
        analyzed = _analyzed(d)
        frust = _truthy(d.get("frustrasi"))
        dur = _num(d.get("durasi"))
        hold = _num(d.get("hold_time_sec"))

        all_theme[theme] += 1
        sent_dist[sent] += 1
        if reso:
            reso_dist[reso] += 1
        if analyzed:
            analyzed_calls += 1
        if frust:
            frustrasi_calls += 1
        dur_sum += dur
        dur_n += 1
        hold_sum += hold
        if day:
            day_calls[day] += 1

        if not ani:
            anon_calls += 1
            if d.get("sid"):
                anon_callers.add(str(d.get("sid")))
            continue

        if day:
            day_callers[day].add(ani)

        u = callers.get(ani)
        if u is None:
            u = callers[ani] = {
                "ani": ani, "calls": 0, "days": set(), "themes": Counter(),
                "sent": Counter(), "reso": Counter(), "agents": Counter(),
                "analyzed": 0, "frustrasi": 0, "dur_sum": 0, "dur_n": 0,
                "hold_sum": 0, "first": day, "last": day, "sids": [],
            }
        u["calls"] += 1
        if day:
            u["days"].add(day)
            u["first"] = min(u["first"] or day, day)
            u["last"] = max(u["last"] or day, day)
        u["themes"][theme] += 1
        u["sent"][sent] += 1
        if reso:
            u["reso"][reso] += 1
        ag = str(d.get("agent_name") or "").strip()
        if ag:
            u["agents"][ag] += 1
        if analyzed:
            u["analyzed"] += 1
        if frust:
            u["frustrasi"] += 1
        u["dur_sum"] += dur
        u["dur_n"] += 1
        u["hold_sum"] += hold
        if len(u["sids"]) < 10 and d.get("sid"):
            u["sids"].append(str(d.get("sid")))

    total_callers = len(callers)
    caller_list = []
    repeat_callers = 0
    multi_day_callers = 0
    for u in callers.values():
        ndays = len(u["days"])
        if u["calls"] > 1:
            repeat_callers += 1
        if ndays > 1:
            multi_day_callers += 1
        caller_list.append({
            "ani": u["ani"], "calls": u["calls"], "days": ndays,
            "themes": ", ".join(k for k, _ in u["themes"].most_common(3)),
            "top_theme": (u["themes"].most_common(1)[0][0] if u["themes"] else "-"),
            "sentiment": (u["sent"].most_common(1)[0][0] if u["sent"] else "-"),
            "resolusi": (u["reso"].most_common(1)[0][0] if u["reso"] else "-"),
            "agent": (u["agents"].most_common(1)[0][0] if u["agents"] else "-"),
            "analyzed": u["analyzed"], "frustrasi": u["frustrasi"],
            "avg_dur": round(u["dur_sum"] / u["dur_n"]) if u["dur_n"] else 0,
            "avg_hold": round(u["hold_sum"] / u["dur_n"]) if u["dur_n"] else 0,
            "first": u["first"], "last": u["last"], "sids": u["sids"],
        })
    caller_list.sort(key=lambda x: (-x["calls"], -x["days"]))

    def _bucket(n):
        if n <= 1:
            return "1x"
        if n == 2:
            return "2x"
        if n <= 5:
            return "3-5x"
        if n <= 10:
            return "6-10x"
        return ">10x"

    freq = Counter()
    for u in callers.values():
        freq[_bucket(u["calls"])] += 1
    freq_dist = [{"label": b, "value": freq.get(b, 0)}
                 for b in ("1x", "2x", "3-5x", "6-10x", ">10x")]

    days_all = set(day_calls) | set(day_callers)
    trend = []
    for day in sorted(x for x in days_all if x):
        trend.append({"day": day, "callers": len(day_callers.get(day, ())),
                      "calls": day_calls.get(day, 0)})

    ndays_active = len([d for d in day_callers if d])
    sum_daily = sum(len(s) for d, s in day_callers.items() if d)
    avg_daily_callers = round(sum_daily / ndays_active, 1) if ndays_active else 0

    counts = sorted(u["calls"] for u in callers.values())

    def _median(a):
        if not a:
            return 0
        n = len(a)
        m = n // 2
        return a[m] if n % 2 else round((a[m - 1] + a[m]) / 2, 1)

    contact_stats = {
        "median": _median(counts),
        "mean": round(sum(counts) / len(counts), 1) if counts else 0,
        "max": counts[-1] if counts else 0,
        "p90": counts[int(0.9 * (len(counts) - 1))] if counts else 0,
    }

    return {
        "ok": True,
        "range": {"start": start or "", "end": end or ""},
        "limit_users": limit_users,
        "callers_truncated": total_callers > limit_users,
        "meta": {"total_calls": total_calls, "total_callers": total_callers,
                 "analyzed_calls": analyzed_calls, "anon_calls": anon_calls,
                 "anon_callers": len(anon_callers), "date_min": dmin,
                 "date_max": dmax, "active_days": ndays_active},
        "kpi": {
            "total_callers": total_callers,
            "avg_daily_callers": avg_daily_callers,
            "repeat_callers": repeat_callers,
            "repeat_pct": round(100 * repeat_callers / total_callers, 1) if total_callers else 0,
            "multi_day_callers": multi_day_callers,
            "total_calls": total_calls,
            "analyzed_calls": analyzed_calls,
            "analyzed_pct": round(100 * analyzed_calls / total_calls, 1) if total_calls else 0,
            "frustrasi_calls": frustrasi_calls,
            "frustrasi_pct": round(100 * frustrasi_calls / total_calls, 1) if total_calls else 0,
            "avg_dur": round(dur_sum / dur_n) if dur_n else 0,
            "avg_hold": round(hold_sum / total_calls) if total_calls else 0,
            "anon_calls": anon_calls,
        },
        "trend": trend,
        "callers": caller_list[:limit_users],
        "freq_dist": freq_dist,
        "contact_stats": contact_stats,
        "themes": [{"label": k, "value": v} for k, v in all_theme.most_common(15)],
        "sentiment": {"Positif": sent_dist.get("Positif", 0),
                      "Netral": sent_dist.get("Netral", 0),
                      "Negatif": sent_dist.get("Negatif", 0),
                      "Tidak diketahui": sent_dist.get("Tidak diketahui", 0)},
        "resolusi": [{"label": k, "value": v} for k, v in reso_dist.most_common(10)],
    }


def caller_conversations(conn, start=None, end=None, ani="", limit=500):
    """Daftar panggilan untuk SATU nomor telepon (lazy-load saat diklik)."""
    if init_phone_db is not None:
        try:
            init_phone_db(conn)
        except Exception:
            pass
    ani = _norm_ani(ani)
    if not ani:
        return ([], False)
    where = ["REPLACE(IFNULL(ani,''),' ','') = ?"]
    params = [ani]
    if start:
        where.append("substr(tanggal,1,10) >= ?")
        params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?")
        params.append(end[:10])
    wsql = " WHERE " + " AND ".join(where)
    rows = conn.execute(
        "SELECT sid,tanggal,agent_name,durasi,hold_time_sec,has_audio,topik,"
        "jenis_layanan,sentiment,emotion,resolusi,frustrasi,ringkasan,analisis_json "
        "FROM awe_phone_interactions" + wsql +
        " ORDER BY tanggal DESC LIMIT ?", params + [limit + 1]
    ).fetchall()
    truncated = len(rows) > limit
    out = []
    for r in rows[:limit]:
        d = dict(r)
        out.append({
            "sid": str(d.get("sid") or ""),
            "tanggal": d.get("tanggal") or "",
            "agent": d.get("agent_name") or "",
            "theme": _theme_of(d),
            "sentiment": _norm_sent(d.get("sentiment")),
            "resolusi": str(d.get("resolusi") or "").strip() or "-",
            "frustrasi": _truthy(d.get("frustrasi")),
            "durasi": _num(d.get("durasi")),
            "has_audio": bool(d.get("has_audio")),
            "analyzed": _analyzed(d),
            "ringkasan": str(d.get("ringkasan") or "").strip(),
        })
    return (out, truncated)
