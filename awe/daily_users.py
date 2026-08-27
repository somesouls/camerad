# -*- coding: utf-8 -*-
"""awe_daily_users.py — Analisis Pengguna Harian AWE (Avaya).

Sub-menu AWE: mengidentifikasi pengguna harian dari tabel awe_conversations,
menghitung jumlah pengguna unik per hari, mengidentifikasi SIAPA (nama + NIK/NPWP
bila terdeteksi), apakah mereka LANGSUNG ke agent (hit 1500200 tanpa lewat bot),
tema/jenis layanan yang dibahas, pengguna berulang, serta indikasi alasan mereka
langsung ke agent.

Catatan data (penting):
  - Identitas: NIK/NPWP diambil dari kolom `nik` di awe_conversations (hasil
    scrape DNIS/nama berformat "Nama [NIK]" saat penarikan data). Isi kurung juga
    bisa berupa penanda teks "NON-NPWP" untuk pengguna tanpa NPWP; ini TIDAK
    memuat nomor unik, jadi pengguna non-NPWP hanya bisa didedup best-effort per
    nama. Data ini murni chat: TIDAK ada ANI/telepon, dan Meta_ss_customerIDs
    (dari Getinteraction) bersifat per-sesi — bukan ID orang lintas percakapan —
    sehingga nama tetap satu-satunya penanda level-orang untuk non-NPWP. Bila
    kolom `nik` kosong (mis. data lama), fallback ke ekstraksi best-effort dari
    transkrip_json (turn milik customer) memakai regex. Nama ada di kolom
    `customer`. Prioritas resolusi identitas: NIK/NPWP > nama > sid (anonim).
  - Baris placeholder/sistem (mis. DNIS=customer -> nama "Customer" tanpa NIK)
    BUKAN pengguna nyata. Baris seperti ini dipisahkan sebagai kategori
    "Tidak Teridentifikasi" dan DIKELUARKAN dari KPI serta agregat pengguna;
    ditampilkan terpisah di kartu + tabel anomali agar tidak menggelembungkan
    angka teridentifikasi.
  - "Langsung ke agent" (hit 1500200 langsung) = behavior in (direct/langsung)
    ATAU deflection_gap=1 (selaras dengan awe.analytics / awe.overview).
  - Daftar percakapan per pengguna TIDAK lagi ditempel di payload utama; dimuat
    lazy lewat /api/awe/daily-users/conversations saat pengguna diklik. Ini
    membuat `limit_users` aman dinaikkan besar tanpa membebani browser.
  - TIDAK ada penggabungan dengan data Dialogflow (tidak ada ID unik lintas
    sumber — sesuai keputusan desain avaya.db).

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


# --- Ekstraksi NIK/NPWP dari transkrip (fallback data lama) ---------------
_NPWP_FMT = _re.compile(r'\b\d{2}\.\d{3}\.\d{3}\.\d[-.\s]?\d{3}\.\d{3}\b')
_DIGITS = _re.compile(r'(?<!\d)(\d{15,16})(?!\d)')
_CUST_ROLES = {"customer", "cust", "pelanggan", "user"}
# Penanda teks non-NPWP di dalam kurung, mis. "Imam Bukhori [NON-NPWP]".
_NONNPWP_RE = _re.compile(r'non[\s\-_]*npwp', _re.I)
# Nama placeholder / sistem (bukan orang nyata) -> kategori Tidak Teridentifikasi.
_PLACEHOLDER_NAMES = {
    "customer", "cust", "pelanggan", "user", "guest", "anonymous", "anonim",
    "unknown", "tidak diketahui", "undefined", "null", "none",
    "n/a", "na", "-", "--", "(customer)", "test",
}


def _norm_name(s):
    return _re.sub(r'\s+', ' ', str(s or "").strip()).lower()


def _is_placeholder_name(s):
    """True bila nama = placeholder/sistem (mis. "Customer" dari DNIS=customer)."""
    return _norm_name(s) in _PLACEHOLDER_NAMES


def _taxtype_of(digits):
    n = len(str(digits or ""))
    if n == 16:
        return "NIK/NPWP"
    if n == 15:
        return "NPWP"
    return "NPWP/NIK"


def _classify_id(raw):
    """Klasifikasi isi kurung kolom `nik`.

    Kembalikan (taxid_digits, idtype, is_non_npwp):
      - Teks 'NON-NPWP' (dan variannya) -> ('', 'NON-NPWP', True); tak ada nomor unik.
      - 16 digit -> (digits, 'NIK/NPWP', False)
      - 15 digit -> (digits, 'NPWP', False)
      - >15 digit -> (digits, 'NPWP/NIK', False)
      - kosong / 0000.. / format lain -> ('', '', False) (tak teridentifikasi)
    """
    s = str(raw or "").strip()
    if not s:
        return ("", "", False)
    if _NONNPWP_RE.search(s):
        return ("", "NON-NPWP", True)
    digits = _re.sub(r'\D', '', s)
    if not digits or set(digits) == {"0"}:
        return ("", "", False)
    if len(digits) == 16:
        return (digits, "NIK/NPWP", False)
    if len(digits) == 15:
        return (digits, "NPWP", False)
    if len(digits) >= 15:
        return (digits, "NPWP/NIK", False)
    return ("", "", False)


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


def daily_users(conn, start=None, end=None, limit_users=2000):
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?"); params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?"); params.append(end[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT sid,tanggal,customer,nik,agent_name,agent_id,durasi,behavior,"
        "is_returning,mapped_intent,coverage_band,case_label,sentiment,emotion,"
        "topik,deflection_gap,jenis_layanan,is_poro,transkrip_json "
        "FROM awe_conversations" + wsql + " ORDER BY tanggal", params
    ).fetchall()

    users = {}
    day_users = defaultdict(set)
    day_conv = Counter()
    day_direct = defaultdict(set)
    day_anom = Counter()
    dmin = dmax = ""
    total_direct_conv = total_reached = 0
    direct_theme = Counter()
    all_theme = Counter()
    direct_sent = Counter()
    poro_direct = 0
    dur_direct_sum = dur_direct_n = 0
    dur_other_sum = dur_other_n = 0
    non_npwp_conv = 0
    non_npwp_theme = Counter()
    idtype_dist = Counter()
    # Penyebab hit agent (agregasi per-tema atas percakapan langsung).
    cause = defaultdict(lambda: {"users": set(), "direct": 0, "neg": 0,
                                 "poro": 0, "dur": 0, "durn": 0})
    # Anomali / "Tidak Teridentifikasi" (mis. DNIS=customer, nama placeholder).
    anom_users = {}
    anom_conv = 0
    anom_theme = Counter()

    for r in rows:
        d = dict(r)
        day = (str(d.get("tanggal") or ""))[:10]
        if day:
            dmin = day if not dmin else min(dmin, day)
            dmax = day if not dmax else max(dmax, day)
        # Identitas: utamakan kolom nik (hasil scrape DNIS). Fallback: transkrip.
        col_nik = str(d.get("nik") or "").strip()
        is_non_npwp = False
        if col_nik:
            taxid, taxtype, is_non_npwp = _classify_id(col_nik)
        else:
            tx = None
            tj = d.get("transkrip_json")
            if tj:
                try:
                    tx = _json.loads(tj)
                except Exception:
                    tx = None
            taxid, taxtype = _extract_taxid(tx)
        name = str(d.get("customer") or "").strip()

        # Klasifikasi identitas. Placeholder (DNIS=customer dll, tanpa NIK &
        # bukan non-NPWP resmi) -> bucket anomali "Tidak Teridentifikasi".
        anomali = False
        if taxid:
            key = "tax:" + taxid; label = taxid; idtype = taxtype or "NPWP/NIK"
        elif is_non_npwp:
            key = ("name:" + _norm_name(name)) if name else ("sid:" + str(d.get("sid") or ""))
            label = name or "(non-NPWP)"; idtype = "NON-NPWP"
        elif name and _is_placeholder_name(name):
            anomali = True
            key = "anom:" + _norm_name(name); label = name
            idtype = "Tidak Teridentifikasi"
        elif name:
            key = "name:" + _norm_name(name); label = name; idtype = "Nama"
        else:
            key = "sid:" + str(d.get("sid") or ""); label = "(anonim)"; idtype = "Anonim"

        direct = _is_direct(d)
        reached = bool(str(d.get("agent_name") or "").strip())
        theme = _theme_of(d)
        sent = _norm_sent(d.get("sentiment"))
        poro = 1 if d.get("is_poro") in (1, "1") else 0
        try:
            dur = int(float(d.get("durasi") or 0))
        except Exception:
            dur = 0

        # --- Baris anomali: catat terpisah, keluarkan dari agregat utama ---
        if anomali:
            anom_conv += 1
            anom_theme[theme] += 1
            if day:
                day_anom[day] += 1
            au = anom_users.get(key)
            if au is None:
                au = anom_users[key] = {
                    "label": label, "conv": 0, "direct": 0, "days": set(),
                    "themes": Counter(), "first": day, "last": day,
                    "reason": "Nama placeholder / DNIS=customer (tanpa NIK)",
                }
            au["conv"] += 1
            if direct:
                au["direct"] += 1
            if day:
                au["days"].add(day)
                au["first"] = min(au["first"] or day, day)
                au["last"] = max(au["last"] or day, day)
            au["themes"][theme] += 1
            continue

        u = users.get(key)
        if u is None:
            u = users[key] = {
                "key": key, "label": label, "idtype": idtype, "name": name,
                "taxid": taxid, "conv": 0, "direct": 0, "reached": 0,
                "days": set(), "themes": Counter(), "sent": Counter(),
                "returning": False, "first": day, "last": day, "sids": [],
                "non_npwp": is_non_npwp,
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
        if is_non_npwp:
            u["non_npwp"] = True
        if len(u["sids"]) < 8 and d.get("sid"):
            u["sids"].append(str(d.get("sid")))

        if day:
            day_users[day].add(key)
            day_conv[day] += 1
            if direct:
                day_direct[day].add(key)
        all_theme[theme] += 1
        idtype_dist[idtype] += 1
        if is_non_npwp:
            non_npwp_conv += 1
            non_npwp_theme[theme] += 1
        if reached:
            total_reached += 1
        if direct:
            total_direct_conv += 1
            direct_theme[theme] += 1
            direct_sent[sent] += 1
            poro_direct += poro
            dur_direct_sum += dur; dur_direct_n += 1
            c = cause[theme]
            c["users"].add(key); c["direct"] += 1
            if sent == "Negatif":
                c["neg"] += 1
            c["poro"] += poro
            c["dur"] += dur; c["durn"] += 1
        else:
            dur_other_sum += dur; dur_other_n += 1

    total_rows = len(rows)
    anom_conv_total = anom_conv
    total_conv = total_rows - anom_conv_total
    total_users = len(users)

    user_list = []
    direct_users = repeat_users = 0
    for u in users.values():
        ndays = len(u["days"])
        if ndays > 1:
            repeat_users += 1
        if u["direct"] > 0:
            direct_users += 1
        user_list.append({
            "label": u["label"], "idtype": u["idtype"], "name": u["name"],
            "taxid": u["taxid"], "conv": u["conv"], "days": ndays,
            "direct": u["direct"],
            "direct_pct": round(100 * u["direct"] / u["conv"], 1) if u["conv"] else 0,
            "reached": u["reached"],
            "themes": ", ".join(k for k, _ in u["themes"].most_common(3)),
            "sentiment": (u["sent"].most_common(1)[0][0] if u["sent"] else "-"),
            "returning": u["returning"], "first": u["first"], "last": u["last"],
            "sids": u["sids"], "non_npwp": u.get("non_npwp", False),
        })

    user_list.sort(key=lambda x: (-x["conv"], -x["direct"]))

    # --- Penyebab hit agent terbanyak (per tema, atas percakapan langsung) ---
    hit_causes = []
    for th, c in cause.items():
        hit_causes.append({
            "theme": th, "users": len(c["users"]), "direct": c["direct"],
            "pct": round(100 * c["direct"] / total_direct_conv, 1) if total_direct_conv else 0,
            "neg_pct": round(100 * c["neg"] / c["direct"], 1) if c["direct"] else 0,
            "poro": c["poro"],
            "avg_dur": round(c["dur"] / c["durn"]) if c["durn"] else 0,
        })
    hit_causes.sort(key=lambda x: -x["direct"])
    hit_causes = hit_causes[:15]

    # --- Distribusi frekuensi kontak per pengguna ---
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
    for u in users.values():
        freq[_bucket(u["conv"])] += 1
    freq_dist = [{"label": b, "value": freq.get(b, 0)}
                 for b in ("1x", "2x", "3-5x", "6-10x", ">10x")]

    # --- Komposisi identitas (per pengguna) ---
    comp = Counter()
    for u in users.values():
        if u["taxid"]:
            comp["NIK/NPWP"] += 1
        elif u.get("non_npwp"):
            comp["NON-NPWP"] += 1
        elif u["idtype"] == "Nama":
            comp["Nama (tanpa penanda)"] += 1
        else:
            comp["Anonim"] += 1
    comp["Anomali"] = len(anom_users)
    identity_comp = [{"label": k, "value": comp[k]}
                     for k in ("NIK/NPWP", "NON-NPWP", "Nama (tanpa penanda)",
                               "Anonim", "Anomali") if comp.get(k)]

    # --- Statistik kontak per pengguna ---
    counts = sorted(u["conv"] for u in users.values())

    def _median(a):
        if not a:
            return 0
        n = len(a); m = n // 2
        return a[m] if n % 2 else round((a[m - 1] + a[m]) / 2, 1)
    contact_stats = {
        "median": _median(counts),
        "mean": round(sum(counts) / len(counts), 1) if counts else 0,
        "max": counts[-1] if counts else 0,
        "p90": counts[int(0.9 * (len(counts) - 1))] if counts else 0,
    }

    ndays_active = len([d for d in day_users if d])
    sum_daily_users = sum(len(s) for d, s in day_users.items() if d)
    avg_daily_users = round(sum_daily_users / ndays_active, 1) if ndays_active else 0

    trend = []
    for day in sorted(day_users):
        if not day:
            continue
        trend.append({"day": day, "users": len(day_users[day]),
                      "conv": day_conv[day], "direct": len(day_direct[day]),
                      "anomali": day_anom.get(day, 0)})

    identified = sum(1 for u in users.values() if u["taxid"] or u["name"])
    with_taxid = sum(1 for u in users.values() if u["taxid"])
    non_npwp_users = sum(1 for u in users.values() if u.get("non_npwp"))

    # --- Tabel anomali / Tidak Teridentifikasi ---
    anom_list = sorted(anom_users.values(), key=lambda x: -x["conv"])
    anomali_out = {
        "conv": anom_conv_total,
        "conv_pct": round(100 * anom_conv_total / total_rows, 1) if total_rows else 0,
        "users": len(anom_users),
        "themes": [{"label": k, "value": v} for k, v in anom_theme.most_common(12)],
        "rows": [{
            "label": u["label"], "conv": u["conv"], "direct": u["direct"],
            "days": len(u["days"]),
            "themes": ", ".join(k for k, _ in u["themes"].most_common(3)),
            "first": u["first"], "last": u["last"], "reason": u["reason"],
        } for u in anom_list],
    }

    return {
        "ok": True,
        "range": {"start": start or "", "end": end or ""},
        "limit_users": limit_users,
        "users_truncated": total_users > limit_users,
        "meta": {"total_conv": total_conv, "total_rows": total_rows,
                 "total_users": total_users,
                 "identified": identified, "with_taxid": with_taxid,
                 "non_npwp_users": non_npwp_users,
                 "anomali_conv": anom_conv_total, "anomali_users": len(anom_users),
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
            "anomali_users": len(anom_users),
            "anomali_conv": anom_conv_total,
        },
        "trend": trend,
        "users": user_list[:limit_users],
        "hit_causes": hit_causes,
        "freq_dist": freq_dist,
        "identity_comp": identity_comp,
        "contact_stats": contact_stats,
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
        "non_npwp": {
            "conv": non_npwp_conv,
            "conv_pct": round(100 * non_npwp_conv / total_conv, 1) if total_conv else 0,
            "users": non_npwp_users,
            "themes": [{"label": k, "value": v} for k, v in non_npwp_theme.most_common(12)],
        },
        "idtype_dist": [{"label": k, "value": v} for k, v in idtype_dist.most_common()],
        "anomali": anomali_out,
    }


def user_conversations(conn, start=None, end=None, taxid="", name="", sid="", limit=800):
    """Daftar percakapan untuk SATU identitas pengguna (lazy-load modal).

    Prioritas filter identitas: taxid (nik) > name (customer) > sid.
    Kembalikan (list_conv, truncated).
    """
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?"); params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?"); params.append(end[:10])
    taxid = _re.sub(r'\D', '', str(taxid or ""))
    name = str(name or "").strip()
    sid = str(sid or "").strip()
    if taxid:
        where.append(
            "REPLACE(REPLACE(REPLACE(REPLACE(IFNULL(nik,''),'.',''),'-',''),' ',''),'/','') LIKE ?")
        params.append("%" + taxid + "%")
    elif name:
        where.append("lower(trim(customer)) = lower(trim(?))"); params.append(name)
    elif sid:
        where.append("sid = ?"); params.append(sid)
    else:
        return ([], False)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT sid,tanggal,agent_name,behavior,deflection_gap,jenis_layanan,"
        "topik,mapped_intent,sentiment FROM awe_conversations" + wsql +
        " ORDER BY tanggal DESC LIMIT ?", params + [limit + 1]
    ).fetchall()
    truncated = len(rows) > limit
    out = []
    for r in rows[:limit]:
        d = dict(r)
        out.append({
            "sid": str(d.get("sid") or ""),
            "tanggal": d.get("tanggal") or "",
            "theme": _theme_of(d),
            "agent_name": d.get("agent_name") or "",
            "direct": _is_direct(d),
            "reached": bool(str(d.get("agent_name") or "").strip()),
            "sentiment": _norm_sent(d.get("sentiment")),
        })
    return (out, truncated)


def register(app, *, render_page):
    """Pasang halaman /awe/pengguna-harian + API daily-users (+ conversations)."""
    async def _page(request: Request):
        return render_page(request, "awe_daily_users.html", "awe_pengguna")

    async def _api(request: Request):
        q = request.query_params
        preset = q.get("range") or "30d"
        start = q.get("start"); end = q.get("end")
        try:
            limit = int(q.get("limit") or 2000)
        except Exception:
            limit = 2000
        limit = max(1, min(limit, 20000))

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                s, e = resolve_range(preset, start, end)
                data = daily_users(conn, s, e, limit_users=limit)
                data["bounds"] = data_bounds(conn)
                data["preset"] = preset
                return data
            finally:
                conn.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

    async def _api_convs(request: Request):
        q = request.query_params
        preset = q.get("range") or "30d"
        start = q.get("start"); end = q.get("end")
        taxid = q.get("taxid") or ""
        name = q.get("name") or ""
        sid = q.get("sid") or ""
        try:
            limit = int(q.get("limit") or 800)
        except Exception:
            limit = 800
        limit = max(1, min(limit, 5000))

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                s, e = resolve_range(preset, start, end)
                convs, truncated = user_conversations(
                    conn, s, e, taxid=taxid, name=name, sid=sid, limit=limit)
                return {"ok": True, "conversations": convs, "truncated": truncated}
            finally:
                conn.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex), "conversations": []},
                                status_code=500)

    app.add_api_route("/awe/pengguna-harian", _page, methods=["GET"])
    app.add_api_route("/api/awe/daily-users", _api, methods=["GET"])
    app.add_api_route("/api/awe/daily-users/conversations", _api_convs, methods=["GET"])
