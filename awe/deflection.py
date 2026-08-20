# -*- coding: utf-8 -*-
"""awe_deflection.py — Deflection Gap → Materi (KPI: pengetahuan bot).

Mencocokkan percakapan AWE (Avaya) yang BERAKHIR DI AGENT dengan Katalog Intent
Dialogflow lewat kemiripan teks (SBERT). Logika §13.4 rencana Camerad Studio:

  - Topik percakapan SUDAH punya intent mirip (>= ambang) tetapi user tetap
    berakhir di agent  => sinyal MATERI bot perlu diperbaiki (deflection gap).
  - Tidak ada intent yang mirip                                => kandidat intent/materi BARU.

Keputusan terkonfirmasi (§13.10): pencocokan berbasis kemiripan topik/teks
(TANPA join ID), halaman sendiri, opsi kecualikan yang langsung minta agent.

Reuse infrastruktur yang sudah ada:
  - avaya_db.awe_conversations (kolom: topik, mapped_intent, behavior,
    agent_name, coverage_band, jenis_layanan, transkrip_json)
  - knowledge_semantic (embedding SBERT + indeks katalog intent, ter-cache)

Endpoint:
  POST /api/awe/deflection-gap/run     -> analisis (JSON)
  GET  /api/awe/deflection-gap/export  -> unduh CSV kandidat materi
Halaman /awe/deflection-gap dirender oleh awe_routes.py (awe_deflection.html).
"""
import csv as _csv
import io as _io
import json as _json
import datetime as _dt
from collections import Counter

import avaya_db as avdb
import knowledge_semantic as ks
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

try:
    import numpy as _np
except Exception:
    _np = None

_DIRECT = ("direct", "langsung")
_CUST_ROLES = {"customer", "cust", "pelanggan", "user"}
_COVERED_BANDS = ("covered", "tinggi", "high", "gray", "grey", "abu", "sedang", "medium")

DEFAULT_MIN_SIM = 0.55
DEFAULT_LIMIT = 1500
MAX_EXAMPLES = 6


# --------------------------------------------------------------------------
# Rentang tanggal (Asia/Jakarta) — selaras dengan awe_analytics.resolve_range
# --------------------------------------------------------------------------
def _jkt_today():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    except Exception:
        tz = _dt.timezone(_dt.timedelta(hours=7))
        return _dt.datetime.now(tz).date()


def resolve_range(preset, start=None, end=None):
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


# --------------------------------------------------------------------------
# Ekstraksi data percakapan
# --------------------------------------------------------------------------
def _fetch_rows(conn, start, end, limit):
    where, params = [], []
    if start:
        where.append("substr(tanggal,1,10) >= ?"); params.append(start[:10])
    if end:
        where.append("substr(tanggal,1,10) <= ?"); params.append(end[:10])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = ("SELECT sid,tanggal,customer,agent_name,behavior,mapped_intent,topik,"
           "coverage_band,deflection_gap,jenis_layanan,transkrip_json "
           "FROM awe_conversations" + wsql +
           " ORDER BY tanggal DESC, rowid DESC LIMIT ?")
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _cust_text(transkrip_json):
    if not transkrip_json:
        return ""
    try:
        tx = _json.loads(transkrip_json)
    except Exception:
        return ""
    parts = []
    for m in tx or []:
        if isinstance(m, dict):
            role = str(m.get("role", "")).strip().lower()
            if role in _CUST_ROLES:
                t = str(m.get("text", "")).strip()
                if t:
                    parts.append(t)
        if len(parts) >= 4:
            break
    return " ".join(parts)[:400]


def _query_text(row):
    return ((row.get("topik") or "").strip()
            or (row.get("mapped_intent") or "").strip()
            or _cust_text(row.get("transkrip_json")))[:400]


def _reached_agent(row):
    return bool((row.get("agent_name") or "").strip())


def _example(row, q, score):
    return {
        "sid": row.get("sid") or "",
        "tanggal": row.get("tanggal") or "",
        "agent": row.get("agent_name") or "",
        "topik": (row.get("topik") or row.get("mapped_intent") or "").strip(),
        "cust": (q or "")[:180],
        "score": (round(float(score), 3) if score is not None else None),
    }


# --------------------------------------------------------------------------
# Analisis inti
# --------------------------------------------------------------------------
def analyze(conn, start=None, end=None, exclude_direct=False,
            min_sim=DEFAULT_MIN_SIM, limit=DEFAULT_LIMIT, max_examples=MAX_EXAMPLES):
    rows = _fetch_rows(conn, start, end, limit)
    items = []
    for r in rows:
        if not _reached_agent(r):
            continue
        beh = (r.get("behavior") or "").strip().lower()
        if exclude_direct and beh in _DIRECT:
            continue
        q = _query_text(r)
        if not q:
            continue
        items.append((r, q))
    total = len(items)

    matched = []    # (row, q, intent, score)
    unmatched = []  # (row, q, intent, score)
    semantic = False

    idx = None
    if ks.is_available() and _np is not None:
        try:
            idx = ks._build_index("katalog")
        except Exception:
            idx = None
    emb = idx.get("emb") if idx else None
    entries = idx.get("entries") if idx else None
    owner = idx.get("owner") if idx else None

    if emb is not None and entries is not None and len(entries) and items:
        semantic = True
        owner_arr = _np.asarray(owner)
        K = len(entries)
        qmat = ks._encode([q for _, q in items])
        if qmat is not None and len(qmat):
            qmat = _np.asarray(qmat, dtype="float32")
            for i, (r, q) in enumerate(items):
                sims = emb @ qmat[i]                 # cosine per-key (emb & q ternormalisasi)
                entry_best = _np.full(K, -1.0, dtype="float32")
                _np.maximum.at(entry_best, owner_arr, sims)
                ei = int(entry_best.argmax())
                s = float(entry_best[ei])
                intent = (entries[ei].get("intent") or "").strip()
                if s >= min_sim and intent:
                    matched.append((r, q, intent, s))
                else:
                    unmatched.append((r, q, intent, s))
    else:
        # Fallback tanpa SBERT: pakai mapped_intent + coverage_band dari pipeline.
        for r, q in items:
            mi = (r.get("mapped_intent") or "").strip()
            band = (r.get("coverage_band") or "").strip().lower()
            gap_flag = r.get("deflection_gap")
            exists = bool(mi) and (band in _COVERED_BANDS or gap_flag in (1, "1"))
            if mi and (exists or band == ""):
                matched.append((r, q, mi, None))
            else:
                unmatched.append((r, q, mi, None))

    # --- Kelompokkan kandidat MATERI (intent sudah ada) per intent ---
    groups = {}
    for r, q, intent, s in matched:
        key = intent or "(tak terpetakan)"
        g = groups.setdefault(key, {"intent": intent, "count": 0, "sim_sum": 0.0,
                                    "nsim": 0, "jenis": Counter(), "examples": []})
        g["count"] += 1
        if s is not None:
            g["sim_sum"] += s; g["nsim"] += 1
        jl = (r.get("jenis_layanan") or "").strip()
        if jl:
            g["jenis"][jl] += 1
        if len(g["examples"]) < max_examples:
            g["examples"].append(_example(r, q, s))
    candidates = []
    for g in groups.values():
        avg = round(g["sim_sum"] / g["nsim"], 3) if g["nsim"] else None
        top_jenis = g["jenis"].most_common(1)[0][0] if g["jenis"] else ""
        candidates.append({"intent": g["intent"], "count": g["count"],
                           "avg_sim": avg, "jenis_layanan": top_jenis,
                           "examples": g["examples"]})
    candidates.sort(key=lambda c: (-c["count"], -(c["avg_sim"] or 0)))

    # --- Kelompokkan KANDIDAT BARU (tanpa intent) per topik ---
    ngroups = {}
    for r, q, intent, s in unmatched:
        key = ((r.get("topik") or "").strip() or (r.get("mapped_intent") or "").strip()
               or (q[:60].strip()) or "(tanpa topik)")
        g = ngroups.setdefault(key, {"topik": key, "count": 0, "sim_sum": 0.0,
                                     "nsim": 0, "examples": []})
        g["count"] += 1
        if s is not None:
            g["sim_sum"] += s; g["nsim"] += 1
        if len(g["examples"]) < max_examples:
            g["examples"].append(_example(r, q, s))
    new_candidates = []
    for g in ngroups.values():
        avg = round(g["sim_sum"] / g["nsim"], 3) if g["nsim"] else None
        new_candidates.append({"topik": g["topik"], "count": g["count"],
                               "avg_sim": avg, "examples": g["examples"]})
    new_candidates.sort(key=lambda c: -c["count"])

    return {
        "ok": True,
        "semantic": semantic,
        "min_sim": round(float(min_sim), 3),
        "exclude_direct": bool(exclude_direct),
        "range": {"start": start or "", "end": end or ""},
        "meta": {
            "total_reached": total,
            "n_gap": len(matched),
            "n_new": len(unmatched),
            "gap_pct": round(100 * len(matched) / total, 1) if total else 0,
            "n_intent_kandidat": len(candidates),
        },
        "candidates": candidates[:200],
        "new_candidates": new_candidates[:100],
        "note": ("" if semantic else
                 "SBERT tidak aktif — memakai pemetaan intent dari pipeline "
                 "(mapped_intent/coverage). Aktifkan KNOWLEDGE_SEMANTIC + "
                 "sentence-transformers untuk pencocokan kemiripan penuh."),
    }


def _params_from(body):
    b = body if isinstance(body, dict) else {}
    preset = str(b.get("range") or "30d")
    start = b.get("start") or ""
    end = b.get("end") or ""
    exclude_direct = str(b.get("exclude_direct") or "").strip().lower() in ("1", "true", "ya", "yes", "on")
    try:
        min_sim = float(b.get("min_sim"))
    except (TypeError, ValueError):
        min_sim = DEFAULT_MIN_SIM
    min_sim = max(0.2, min(0.95, min_sim))
    try:
        limit = int(b.get("limit"))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(5000, limit))
    s, e = resolve_range(preset, start, end)
    return {"preset": preset, "start": s, "end": e,
            "exclude_direct": exclude_direct, "min_sim": min_sim, "limit": limit}


def _run_analysis(p):
    conn = avdb.init_db(avdb.connect())
    try:
        data = analyze(conn, start=p["start"], end=p["end"],
                       exclude_direct=p["exclude_direct"], min_sim=p["min_sim"],
                       limit=p["limit"])
        data["preset"] = p["preset"]
        return data
    finally:
        conn.close()


def register(app):
    """Pasang API Deflection Gap → Materi. Halaman /awe/deflection-gap dirender
    oleh awe_routes.py (template awe_deflection.html)."""

    async def api_run(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        p = _params_from(body)
        try:
            return JSONResponse(await run_in_threadpool(_run_analysis, p))
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

    async def api_export(request: Request):
        q = request.query_params
        p = _params_from({
            "range": q.get("range"), "start": q.get("start"), "end": q.get("end"),
            "exclude_direct": q.get("exclude_direct"), "min_sim": q.get("min_sim"),
            "limit": q.get("limit"),
        })
        try:
            data = await run_in_threadpool(_run_analysis, p)
        except Exception as ex:
            return Response(content="error: %s" % ex, media_type="text/plain",
                            status_code=500)
        buf = _io.StringIO()
        w = _csv.writer(buf, quoting=_csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(["Intent (sudah ada)", "Jumlah ke Agent", "Rata2 Kemiripan",
                    "Jenis Layanan Dominan", "Contoh SID"])
        for c in data.get("candidates", []):
            sids = "; ".join(e.get("sid", "") for e in c.get("examples", []))
            w.writerow([c.get("intent", ""), c.get("count", 0),
                        (c.get("avg_sim") if c.get("avg_sim") is not None else ""),
                        c.get("jenis_layanan", ""), sids])
        w.writerow([])
        w.writerow(["Kandidat Materi/Intent BARU (belum ada intent)", "Jumlah", "", "", "Contoh SID"])
        for c in data.get("new_candidates", []):
            sids = "; ".join(e.get("sid", "") for e in c.get("examples", []))
            w.writerow([c.get("topik", ""), c.get("count", 0), "", "", sids])
        payload = "\ufeff" + buf.getvalue()
        headers = {"Content-Disposition": 'attachment; filename="deflection_gap_materi.csv"'}
        return Response(content=payload, media_type="text/csv; charset=utf-8",
                        headers=headers)

    app.add_api_route("/api/awe/deflection-gap/run", api_run, methods=["POST"])
    app.add_api_route("/api/awe/deflection-gap/export", api_export, methods=["GET"])
