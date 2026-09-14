# -*- coding: utf-8 -*-
"""Menu 'Detail Percakapan' Dialogflow (ChatBot Kring Pajak - Wajib Pajak).

Membaca log percakapan chatbot (profil 'chatbot') dari db.agent_log_db
(rag_chat_log), dikelompokkan per percakapan (conv_id; fallback per giliran),
dengan filter tanggal + pencarian isi (keyword / email trik-titik Gmail).

Halaman & API BARU - TIDAK menyentuh alur webhook/echo/replay yang sudah ada.
Semua operasi READ-ONLY atas rag_chat_log.

Rute (area akses 'dialogflow' via app_core._route_area default; aksi 'read'):
  GET /dialogflow/percakapan                 -> halaman
  GET /api/dialogflow/percakapan/list        -> daftar percakapan (filter+cari)
  GET /api/dialogflow/percakapan/detail      -> giliran satu percakapan (+waktu)
  GET /api/dialogflow/percakapan/export.csv  -> ekspor CSV hasil (feeding TIK)
"""
import csv
import io
import datetime as _dt
from collections import Counter

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page
import db.agent_log_db as aldb
import awe.content_search as csearch

# Batas baris log yang dipindai per query (jaga memori & waktu respons).
_MAX_SCAN = 20000
_JKT = _dt.timezone(_dt.timedelta(hours=7))


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _day_start_utc(d):
    """UTC datetime utk tengah malam Asia/Jakarta pada tanggal d."""
    midnight_jkt = _dt.datetime(d.year, d.month, d.day, tzinfo=_JKT)
    return midnight_jkt.astimezone(_dt.timezone.utc)


def _range_utc(preset, start="", end=""):
    """[lo,hi) string UTC 'YYYY-MM-DD HH:MM:SS' utk hari kalender Asia/Jakarta.

    ts di rag_chat_log disimpan UTC. (None, None) berarti semua waktu.
    """
    preset = (preset or "all").strip().lower()
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    today = now_utc.astimezone(_JKT).date()

    if preset in ("", "all", "semua"):
        return None, None
    if preset == "custom":
        def parse_d(s):
            try:
                y, m, d = [int(x) for x in str(s)[:10].split("-")]
                return _dt.date(y, m, d)
            except Exception:
                return None
        ds = parse_d(start) or today
        de = parse_d(end) or ds
        if de < ds:
            ds, de = de, ds
        lo = _day_start_utc(ds)
        hi = _day_start_utc(de) + _dt.timedelta(days=1)
        return _fmt(lo), _fmt(hi)
    if preset in ("today", "hari-ini"):
        lo = _day_start_utc(today)
        return _fmt(lo), _fmt(lo + _dt.timedelta(days=1))
    if preset in ("yesterday", "kemarin"):
        y = today - _dt.timedelta(days=1)
        lo = _day_start_utc(y)
        return _fmt(lo), _fmt(lo + _dt.timedelta(days=1))
    days = {"7d": 7, "30d": 30, "90d": 90}.get(preset, 0)
    if days <= 0:
        return None, None
    lo = _day_start_utc(today - _dt.timedelta(days=days - 1))
    hi = _day_start_utc(today) + _dt.timedelta(days=1)
    return _fmt(lo), _fmt(hi)


def _to_wib(ts):
    """Konversi ts UTC 'YYYY-MM-DD HH:MM:SS' -> string WIB (+7). Toleran error."""
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        dt = _dt.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=_dt.timezone.utc).astimezone(_JKT)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def _dur_seconds(a, b):
    try:
        fa = _dt.datetime.strptime(str(a)[:19], "%Y-%m-%d %H:%M:%S")
        fb = _dt.datetime.strptime(str(b)[:19], "%Y-%m-%d %H:%M:%S")
        return max(0, int((fb - fa).total_seconds()))
    except Exception:
        return 0


def _single_keyword_like(content, mode):
    """Bila mode keyword & query 1 term tunggal, kembalikan string LIKE utk
    pre-filter SQL (percepat). Selain itu None (pindai lebih luas)."""
    if csearch.is_specific_mode(mode):
        return None
    terms = csearch._keyword_terms(content)
    if len(terms) == 1 and " " not in terms[0]:
        return terms[0]
    return None


def _fetch_turns(lo, hi, keyword_like=None):
    """Ambil giliran chatbot (READ-ONLY) dari rag_chat_log dalam [lo,hi)."""
    c = aldb.init_db(aldb.connect())
    try:
        where = ["profil='chatbot'"]
        params = []
        if lo:
            where.append("ts>=?"); params.append(lo)
        if hi:
            where.append("ts<?"); params.append(hi)
        if keyword_like:
            where.append("(lower(coalesce(question,'')) LIKE ? OR "
                         "lower(coalesce(answer,'')) LIKE ?)")
            like = "%" + keyword_like + "%"
            params.append(like); params.append(like)
        sql = ("SELECT id, ts, username, question, answer, domain, feedback, "
               "grounded, conv_id FROM rag_chat_log WHERE " +
               " AND ".join(where) + " ORDER BY ts ASC LIMIT ?")
        params.append(_MAX_SCAN)
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def _build_conversations(turns, content="", mode="keyword"):
    """Kelompokkan giliran menjadi percakapan + terapkan pencarian isi."""
    groups = {}
    order = []
    for t in turns:
        cid = (str(t.get("conv_id") or "").strip()) or ("turn-" + str(t.get("id")))
        g = groups.get(cid)
        if g is None:
            g = []
            groups[cid] = g
            order.append(cid)
        g.append(t)

    do_filter = bool(str(content or "").strip()) or csearch.is_specific_mode(mode)
    out = []
    for cid in order:
        tl = groups[cid]
        matched_turns = 0
        emails = {}
        for t in tl:
            blob = (str(t.get("question") or "") + "\n" + str(t.get("answer") or ""))
            if do_filter and csearch.text_matches(blob, content, mode):
                matched_turns += 1
            # Sinyal calo: selalu kumpulkan email Gmail trik-titik utk kolom.
            for h in csearch.find_emails(blob, dot_trick_only=True):
                emails[h["raw"]] = h["normalized"]
        if do_filter and matched_turns == 0:
            continue
        tss = [str(t.get("ts") or "") for t in tl if t.get("ts")]
        waktu = tss[0] if tss else ""
        waktu_akhir = tss[-1] if tss else ""
        domains = Counter((str(t.get("domain") or "").strip())
                          for t in tl if str(t.get("domain") or "").strip())
        topik = domains.most_common(1)[0][0] if domains else ""
        preview = ""
        for t in tl:
            q = str(t.get("question") or "").strip()
            if q:
                preview = q[:200]
                break
        out.append({
            "conv_id": cid,
            "username": str(tl[0].get("username") or "").strip(),
            "waktu": _to_wib(waktu),
            "waktu_akhir": _to_wib(waktu_akhir),
            "durasi": _dur_seconds(waktu, waktu_akhir),
            "n_turns": len(tl),
            "topik": topik,
            "preview": preview,
            "emails": [{"raw": k, "normalized": v} for k, v in emails.items()],
            "matched_turns": matched_turns,
        })
    # Terbaru di atas.
    out.sort(key=lambda x: x.get("waktu") or "", reverse=True)
    return out


def _fetch_detail(conv_id):
    """Kembalikan giliran satu percakapan (READ-ONLY) beserta waktu WIB."""
    cid = str(conv_id or "").strip()
    if not cid:
        return []
    c = aldb.init_db(aldb.connect())
    try:
        if cid.startswith("turn-"):
            try:
                rid = int(cid.split("-", 1)[1])
            except Exception:
                return []
            rows = c.execute(
                "SELECT id, ts, username, role, question, answer, domain, grounded "
                "FROM rag_chat_log WHERE id=? AND profil='chatbot'", (rid,)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, ts, username, role, question, answer, domain, grounded "
                "FROM rag_chat_log WHERE conv_id=? AND profil='chatbot' "
                "ORDER BY ts ASC, id ASC", (cid,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            out.append({
                "id": d.get("id"),
                "waktu": _to_wib(d.get("ts")),
                "username": d.get("username") or "",
                "question": d.get("question") or "",
                "answer": d.get("answer") or "",
                "domain": d.get("domain") or "",
                "grounded": d.get("grounded"),
            })
        return out
    finally:
        c.close()


def register(app):
    """Pasang halaman + API Detail Percakapan Dialogflow ke FastAPI app."""

    async def page_percakapan(request: Request):
        return render_page(request, "df_percakapan.html", "df_percakapan")

    app.add_api_route("/dialogflow/percakapan", page_percakapan, methods=["GET"])

    async def api_list(request: Request):
        q = request.query_params
        rng = (q.get("range") or "30d").strip()
        start = (q.get("start") or "").strip()
        end = (q.get("end") or "").strip()
        content = (q.get("content") or q.get("q") or "").strip()
        mode = (q.get("mode") or "keyword").strip()
        try:
            limit = min(int(q.get("limit") or 500), 2000)
        except Exception:
            limit = 500

        def _run():
            lo, hi = _range_utc(rng, start, end)
            like = _single_keyword_like(content, mode)
            turns = _fetch_turns(lo, hi, keyword_like=like)
            convs = _build_conversations(turns, content, mode)
            scanned = len(turns)
            return convs, scanned

        try:
            convs, scanned = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)
        truncated = scanned >= _MAX_SCAN
        return JSONResponse({
            "ok": True,
            "total": len(convs),
            "scanned": scanned,
            "truncated": truncated,
            "conversations": convs[:limit],
        })

    app.add_api_route("/api/dialogflow/percakapan/list", api_list, methods=["GET"])

    async def api_detail(request: Request):
        conv_id = (request.query_params.get("conv_id") or "").strip()
        if not conv_id:
            return JSONResponse({"ok": False, "error": "conv_id kosong."},
                                status_code=400)

        def _run():
            return _fetch_detail(conv_id)

        try:
            turns = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)
        return JSONResponse({"ok": True, "conv_id": conv_id,
                             "turns": turns, "total": len(turns)})

    app.add_api_route("/api/dialogflow/percakapan/detail", api_detail,
                      methods=["GET"])

    async def api_export(request: Request):
        q = request.query_params
        rng = (q.get("range") or "30d").strip()
        start = (q.get("start") or "").strip()
        end = (q.get("end") or "").strip()
        content = (q.get("content") or q.get("q") or "").strip()
        mode = (q.get("mode") or "keyword").strip()

        def _run():
            lo, hi = _range_utc(rng, start, end)
            like = _single_keyword_like(content, mode)
            turns = _fetch_turns(lo, hi, keyword_like=like)
            return _build_conversations(turns, content, mode)

        try:
            convs = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["waktu_wib", "conv_id", "pengguna", "topik", "durasi_detik",
                    "jumlah_giliran", "email_terdeteksi", "email_kanonik",
                    "cuplikan"])
        for cvo in convs:
            raw_emails = "; ".join(e["raw"] for e in cvo.get("emails", []))
            norm_emails = "; ".join(sorted({e["normalized"]
                                            for e in cvo.get("emails", [])}))
            w.writerow([cvo.get("waktu", ""), cvo.get("conv_id", ""),
                        cvo.get("username", ""), cvo.get("topik", ""),
                        cvo.get("durasi", 0), cvo.get("n_turns", 0),
                        raw_emails, norm_emails, cvo.get("preview", "")])
        buf.seek(0)
        fname = "detail_percakapan_dialogflow.csv"
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=" + fname})

    app.add_api_route("/api/dialogflow/percakapan/export.csv", api_export,
                      methods=["GET"])
