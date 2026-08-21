# -*- coding: utf-8 -*-
"""awe_daily_users.py — Analisis Pengguna Harian AWE (Avaya).

Sub-menu AWE baru: mengidentifikasi pengguna harian dari tabel awe_conversations,
menghitung jumlah pengguna unik per hari, mengidentifikasi SIAPA (nama + NIK/NPWP
bila terdeteksi di transkrip), apakah mereka LANGSUNG ke agent (hit 1500200 tanpa
lewat bot), tema/jenis layanan yang dibahas, pengguna berulang, serta indikasi
alasan mereka langsung ke agent.

Catatan data (penting):
  - Tabel awe_conversations TIDAK punya kolom NIK/NPWP. Kolom identitas yang ada
    hanya `customer` (nama). NIK/NPWP diekstraksi best-effort dari transkrip_json
    (turn milik customer) memakai regex. Identitas pengguna diresolusi dengan
    prioritas: NPWP/NIK > nama > sid (anonim).
  - "Langsung ke agent" (hit 1500200 langsung) = behavior in (direct/langsung)
    ATAU deflection_gap=1 (selaras dengan awe.analytics / awe.overview).
  - TIDAK ada penggabungan dengan data Dialogflow (tidak ada ID unik lintas
    sumber — sesuai keputusan desain avaya.db). Percakapan AWE sudah mencakup
    giliran chatbot + agent, jadi analisis ini berdiri sendiri di atas AWE.

Dipasang via awe.analytics.register() (yang sudah memegang render_page):
    import awe.daily_users as awe_daily_users
    awe_daily_users.register(app, render_page=render_page)
"""
import re as _re
import json as _json
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
    """Kembalikan (start,end) 'YYYY-MM-DD'. (None,None) = semua. Default 30d."""
    preset = (preset or "30d").strip().lower()
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
    days = {"7d": 7, "30d": 30, "90d": 90}.get(preset, 30)
    return (iso(today - _dt.timedelta(days=days - 1)), iso(today))


def data_bounds(conn):
    try:
        row = conn.execute(
            "SELECT MIN(substr(tanggal,1,10)) AS a, MAX(substr(tanggal,1,10)) AS b "
            "FROM awe_conversations"
        ).fetchone()
        return {"min": (row["a"] or ""), "max": (row["b"] or "")}
    except Exception:
        return {"min": "", "max": ""}


# --- Ekstraksi NIK/NPWP dari transkrip -----------------------------------
_NPWP_FMT = _re.compile(r'\b\d{2}\.\d{3}\.\d{3}\.\d[-.\s]?\d{3}\.\d{3}\b')
_DIGITS = _re.compile(r'(?<!\d)(\d{15,16})(?!\d)')
_CUST_ROLES = {"customer", "cust", "pelanggan", "user"}


def _norm_name(s):
    return _re.sub(r'\s+', ' ', str(s or "").strip()).lower()


def _extract_taxid(transkrip):
    """Kembalikan (digit_taxid, tipe) dari transkrip; utamakan turn customer."""
    if not transkrip:
        return ("", "")
    cust_texts, all_texts = [], []
    for m in transkrip:
        if not isinstance(m, dict):
            continue
        t = str(m.get("text", "") or "")
        all_texts.append(t)
        if str(m.get("role", "")).strip().lower() in _CUST_ROLES:
            cust_texts.append(t)
    for texts in (cust_texts, all_texts):
        blob = "\n".join(texts)
        mm = _NPWP_FMT.search(blob)
        if mm:
            digits = _re.sub(r'\D', '', mm.group(0))
            if len(digits) in (15, 16):
                return (digits, "NPWP" if len(digits) == 15 else "NIK/NPWP")
        mm = _DIGITS.search(blob)
        if mm:
            digits = mm.group(1)
            return (digits, "NIK/NPWP" if len(digits) == 16 else "NPWP")
    return ("", "")


def _is_direct(d):
    beh = str(d.get("behavior") or "").strip().lower()
    if beh in ("direct", "langsung"):
        return True
    return str(d.get("deflection_gap")).strip().lower() in ("1", "true", "ya", "yes", "y")


def _theme_of(d):
    return (str(d.get("jenis_layanan") or "").strip()
            or (str(d.get("topik")).strip() if d.get("topik") else "")
            or str(d.get("mapped_intent") or "").strip()
            or "(tanpa tema)")


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "ya", "yes", "y")


def _norm_sent(s):
    s = (s or "").strip().lower()
    if s.startswith("pos"):
        return "Positif"
    if s.startswith("neg"):
        return "Negatif"
    if s.startswith("net") or s.startswith("neu"):
        return "Netral"
    return "Tidak diketahui"


def daily_users(conn, start=None, end=None, limit_users=400, limit_conv=400):
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?"); params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?"); params.append(end[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT sid,tanggal,customer,agent_name,agent_id,durasi,behavior,"
        "is_returning,mapped_intent,coverage_band,case_label,sentiment,emotion,"
        "topik,deflection_gap,jenis_layanan,is_poro,transkrip_json "
        "FROM awe_conversations" + wsql + " ORDER BY tanggal", params
    ).fetchall()

    users = {}
    day_users = defaultdict(set)
    day_conv = Counter()
    day_direct = defaultdict(set)
    dmin = dmax = ""
    total_direct_conv = total_reached = 0
    direct_theme = Counter()
    all_theme = Counter()
    direct_sent = Counter()
    poro_direct = 0
    dur_direct_sum = dur_direct_n = 0
    dur_other_sum = dur_other_n = 0
    conv_out = []

    for r in rows:
        d = dict(r)
        day = (str(d.get("tanggal") or ""))[:10]
        if day:
            dmin = day if not dmin else min(dmin, day)
            dmax = day if not dmax else max(dmax, day)
        tx = None
        tj = d.get("transkrip_json")
        if tj:
            try:
                tx = _json.loads(tj)
            except Exception:
                tx = None
        taxid, taxtype = _extract_taxid(tx)
        name = str(d.get("customer") or "").strip()
        if taxid:
            key = "tax:" + taxid; label = taxid; idtype = taxtype or "NPWP/NIK"
        elif name:
            key = "name:" + _norm_name(name); label = name; idtype = "Nama"
        else:
            key = "sid:" + str(d.get("sid") or ""); label = "(anonim)"; idtype = "Anonim"

        direct = _is_direct(d)
        reached = bool(str(d.get("agent_name") or "").strip())
        theme = _theme_of(d)
        sent = _norm_sent(d.get("sentiment"))
        try:
            dur = int(float(d.get("durasi") or 0))
        except Exception:
            dur = 0

        u = users.get(key)
        if u is None:
            u = users[key] = {
                "key": key, "label": label, "idtype": idtype, "name": name,
                "taxid": taxid, "conv": 0, "direct": 0, "reached": 0,
                "days": set(), "themes": Counter(), "sent": Counter(),
                "returning": False, "first": day, "last": day, "sids": [],
            }
        u["conv"] += 1
        if direct:
            u["direct"] += 1
        if reached:
            u["reached"] += 1
        if day:
            u["days"].add(day)
            u["first"] = min(u["first"] or day, day)
            u["last"] = max(u["last"] or day, day)
        u["themes"][theme] += 1
        u["sent"][sent] += 1
        if _truthy(d.get("is_returning")):
            u["returning"] = True
        if not u["name"] and name:
            u["name"] = name
        if len(u["sids"]) < 8 and d.get("sid"):
            u["sids"].append(str(d.get("sid")))

        if day:
            day_users[day].add(key)
            day_conv[day] += 1
            if direct:
                day_direct[day].add(key)
        all_theme[theme] += 1
        if reached:
            total_reached += 1
        if direct:
            total_direct_conv += 1
            direct_theme[theme] += 1
            direct_sent[sent] += 1
            if d.get("is_poro") in (1, "1"):
                poro_direct += 1
            dur_direct_sum += dur; dur_direct_n += 1
        else:
            dur_other_sum += dur; dur_other_n += 1

        if len(conv_out) < limit_conv:
            conv_out.append({
                "tanggal": d.get("tanggal") or "", "sid": d.get("sid") or "",
                "user": label, "idtype": idtype, "taxid": taxid,
                "customer": name, "agent_name": d.get("agent_name") or "",
                "direct": direct, "reached": reached, "theme": theme,
                "sentiment": sent,
            })

    total_conv = len(rows)
    total_users = len(users)

    user_list = []
    direct_users = repeat_users = 0
    repeat_direct = []
    for u in users.values():
        ndays = len(u["days"])
        if ndays > 1:
            repeat_users += 1
        if u["direct"] > 0:
            direct_users += 1
        item = {
            "label": u["label"], "idtype": u["idtype"], "name": u["name"],
            "taxid": u["taxid"], "conv": u["conv"], "days": ndays,
            "direct": u["direct"],
            "direct_pct": round(100 * u["direct"] / u["conv"], 1) if u["conv"] else 0,
            "reached": u["reached"],
            "themes": ", ".join(k for k, _ in u["themes"].most_common(3)),
            "sentiment": (u["sent"].most_common(1)[0][0] if u["sent"] else "-"),
            "returning": u["returning"], "first": u["first"], "last": u["last"],
            "sids": u["sids"],
        }
        user_list.append(item)
        if u["direct"] > 0 and (ndays > 1 or u["direct"] > 1):
            repeat_direct.append(item)

    user_list.sort(key=lambda x: (-x["conv"], -x["direct"]))
    repeat_direct.sort(key=lambda x: (-x["direct"], -x["days"]))

    ndays_active = len([d for d in day_users if d])
    sum_daily_users = sum(len(s) for d, s in day_users.items() if d)
    avg_daily_users = round(sum_daily_users / ndays_active, 1) if ndays_active else 0

    trend = []
    for day in sorted(day_users):
        if not day:
            continue
        trend.append({"day": day, "users": len(day_users[day]),
                      "conv": day_conv[day], "direct": len(day_direct[day])})

    identified = sum(1 for u in users.values() if u["taxid"] or u["name"])
    with_taxid = sum(1 for u in users.values() if u["taxid"])

    return {
        "ok": True,
        "range": {"start": start or "", "end": end or ""},
        "meta": {"total_conv": total_conv, "total_users": total_users,
                 "identified": identified, "with_taxid": with_taxid,
                 "date_min": dmin, "date_max": dmax, "active_days": ndays_active},
        "kpi": {
            "total_users": total_users,
            "avg_daily_users": avg_daily_users,
            "direct_users": direct_users,
            "direct_users_pct": round(100 * direct_users / total_users, 1) if total_users else 0,
            "direct_conv": total_direct_conv,
            "direct_conv_pct": round(100 * total_direct_conv / total_conv, 1) if total_conv else 0,
            "repeat_users": repeat_users,
            "identified_pct": round(100 * identified / total_users, 1) if total_users else 0,
            "with_taxid_pct": round(100 * with_taxid / total_users, 1) if total_users else 0,
        },
        "trend": trend,
        "users": user_list[:limit_users],
        "repeat_direct": repeat_direct[:50],
        "direct_focus": {
            "themes": [{"label": k, "value": v} for k, v in direct_theme.most_common(12)],
            "sentiment": {"Positif": direct_sent.get("Positif", 0),
                          "Netral": direct_sent.get("Netral", 0),
                          "Negatif": direct_sent.get("Negatif", 0)},
            "poro": poro_direct,
            "avg_dur_direct": round(dur_direct_sum / dur_direct_n) if dur_direct_n else 0,
            "avg_dur_other": round(dur_other_sum / dur_other_n) if dur_other_n else 0,
        },
        "themes": [{"label": k, "value": v} for k, v in all_theme.most_common(12)],
        "conversations": conv_out,
    }


def register(app, *, render_page):
    """Pasang halaman /awe/pengguna-harian + API /api/awe/daily-users."""
    async def _page(request: Request):
        return render_page(request, "awe_daily_users.html", "awe_pengguna")

    async def _api(request: Request):
        q = request.query_params
        preset = q.get("range") or "30d"
        start = q.get("start"); end = q.get("end")

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                s, e = resolve_range(preset, start, end)
                data = daily_users(conn, s, e)
                data["bounds"] = data_bounds(conn)
                data["preset"] = preset
                return data
            finally:
                conn.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

    app.add_api_route("/awe/pengguna-harian", _page, methods=["GET"])
    app.add_api_route("/api/awe/daily-users", _api, methods=["GET"])
