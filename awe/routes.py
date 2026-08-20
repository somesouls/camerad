# -*- coding: utf-8 -*-
"""awe_routes.py — Rute AWE Avaya: halaman menu, daftar/riwayat analisis,
tarik langsung (pull), staging (Kelola Data AWE Tahap 1), dan proses bahasa
(Kelola Data AWE Tahap 2). Migrasi langkah 5 dari web_app.py.

Sejumlah helper pipeline studio (save_artifact, load_state, save_state, Ctx,
avaya2_pull_intents, avaya3_start, avaya3_fetch, api_endpoint, curl_json_raw)
masih berada di web_app.py sampai langkah 6, jadi di-inject lewat register().

Daftarkan dengan:
    import awe.routes as awe_routes
    awe_routes.register(app, save_artifact=..., load_state=..., ...)
"""
import re
import json
import threading as _threading
import uuid as _uuid

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool

import avaya.db as avdb
import avaya.client as avc
from app_core import CONFIG, render_page

# Helper pipeline studio yang di-inject dari web_app.py saat register() (lihat langkah 6).
save_artifact = None
load_state = None
save_state = None
Ctx = None
avaya2_pull_intents = None
avaya3_start = None
avaya3_fetch = None
api_endpoint = None
curl_json_raw = None


async def awe_kelola_page(request: Request):
    return render_page(request, "awe_kelola.html", "awe_kelola")


async def awe_penilaian_page(request: Request):
    return render_page(request, "awe_penilaian.html", "awe_penilaian")


async def awe_deflection_gap_page(request: Request):
    return RedirectResponse("/awe/coverage", status_code=302)



async def awe_page(request: Request):
    """Menu Analisis AWE Avaya (terpisah dari Dialogflow)."""
    return render_page(request, "awe.html", "awe")


async def awe_list_runs():
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return {"ok": True, "runs": avdb.list_runs(conn), "stats": avdb.stats(conn)}
        finally:
            conn.close()
    return JSONResponse(await run_in_threadpool(_do))


async def awe_get_run(id: str = ""):
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.get_run(conn, id, with_records=False)
        finally:
            conn.close()
    r = await run_in_threadpool(_do)
    if not r:
        return JSONResponse({"ok": False, "error": "Analisis tidak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "run": r})


async def awe_run_dashboard(id: str = ""):
    def _get():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.get_run(conn, id)
        finally:
            conn.close()
    r = await run_in_threadpool(_get)
    if not r:
        return PlainTextResponse("Analisis tidak ditemukan.", status_code=404)
    endpoint = api_endpoint(CONFIG, "", "/api/avaya-render")
    payload = json.dumps({"dashboard": r["dashboard"]}, ensure_ascii=False)
    try:
        html = await run_in_threadpool(curl_json_raw, CONFIG, endpoint, payload)
        return Response(content=html, media_type="text/html; charset=utf-8")
    except Exception as _e:
        return PlainTextResponse("Backend AWE belum aktif (jalankan avaya_pipeline di :8000). Detail: %r" % _e, status_code=502)


async def awe_delete_run(request: Request):
    body = await request.json()
    rid = (body or {}).get("id", "")
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.delete_run(conn, rid)
        finally:
            conn.close()
    n = await run_in_threadpool(_do)
    return JSONResponse({"ok": True, "deleted": n})



# =============================================================
# AWE §11.3 — Tarik data langsung tanpa extension
#   - kredensial pegawai TIDAK disimpan (hanya di memori proses saat menarik)
#   - cek cakupan tanggal dulu; kalau sudah ada, pakai data yang ada
# =============================================================
_AWE_PULL_JOBS = {}
_AWE_PULL_LOCK = _threading.Lock()


def _awe_job_set(job_id, **kw):
    with _AWE_PULL_LOCK:
        j = _AWE_PULL_JOBS.setdefault(job_id, {})
        j.update(kw)


def _awe_job_get(job_id):
    with _AWE_PULL_LOCK:
        j = _AWE_PULL_JOBS.get(job_id)
        return dict(j) if j else {}


def _awe_pull_worker(job_id, cfg, run, date_from, date_to, username, password, base_url):
    """Login → tarik rentang tanggal → tulis step12 gabungan. Kredensial dilupakan."""
    logs = []
    def prog(m):
        logs.append(m)
        _awe_job_set(job_id, message=m, log=logs[-10:])
    try:
        _awe_job_set(job_id, status="login", message="Login ke Avaya WFO…")
        client = avc.AvayaClient(base_url=(base_url or None))
        client.login(username, password)
        username = None
        password = None  # jangan simpan kredensial
        _awe_job_set(job_id, status="pull", message="Menarik data…")
        convs = client.pull_range(date_from, date_to, on_prog=prog)
        payload = json.dumps({"data": convs}, ensure_ascii=False)
        summary = {
            "status": "Selesai",
            "sumber": "Tarik langsung (tanpa extension)",
            "rentang_tanggal": "%s s/d %s" % (date_from, date_to),
            "total_percakapan": len(convs),
        }
        save_artifact(cfg, run, 12, "json", payload, "avaya_gabungan.json", summary)
        st = load_state(cfg, run)
        st["awe_pull_range"] = [str(date_from)[:10], str(date_to)[:10]]
        save_state(cfg, run, st)
        _awe_job_set(job_id, status="done", finished=True, ok=True, run=run,
                     n_conv=len(convs),
                     message="Berhasil menarik %d percakapan. Lanjut analisis…" % len(convs))
    except avc.AvayaAuthError as e:
        _awe_job_set(job_id, status="error", finished=True, ok=False,
                     need_login=True, error=str(e))
    except Exception as e:
        _awe_job_set(job_id, status="error", finished=True, ok=False,
                     need_login=False, error=str(e))


async def awe_pull_check(request: Request):
    body = await request.json() or {}
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal (dari & sampai) wajib diisi."}, status_code=400)
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.coverage_for_range(conn, df, dt)
        finally:
            conn.close()
    cov = await run_in_threadpool(_do)
    cov["ok"] = True
    cov["fully_covered"] = (len(cov.get("missing", [])) == 0)
    cov["need_login"] = (len(cov.get("missing", [])) > 0)
    return JSONResponse(cov)


async def awe_pull_start(request: Request):
    body = await request.json() or {}
    run = str(body.get("run") or "").strip()
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    username = body.get("username") or ""
    password = body.get("password") or ""
    base_url = str(body.get("base_url") or "").strip()
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", run):
        return JSONResponse({"ok": False, "error": "Run ID tidak valid."}, status_code=400)
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal wajib diisi."}, status_code=400)
    if not password:
        return JSONResponse({"ok": False, "error": "Password AWE wajib diisi.", "need_login": True}, status_code=400)
    job_id = _uuid.uuid4().hex
    _awe_job_set(job_id, status="queued", finished=False, ok=None, message="Menyiapkan…")
    _threading.Thread(
        target=_awe_pull_worker,
        args=(job_id, CONFIG, run, df, dt, username, password, base_url),
        daemon=True,
    ).start()
    # username/password hanya diteruskan ke thread lalu dilupakan; tidak ditulis ke disk/DB
    return JSONResponse({"ok": True, "job": job_id})


async def awe_pull_progress(job: str = ""):
    j = _awe_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "progress": j})


async def awe_pull_fetch(job: str = ""):
    j = _awe_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    if not j.get("finished"):
        return JSONResponse({"ok": True, "pending": True, "progress": j})
    with _AWE_PULL_LOCK:
        _AWE_PULL_JOBS.pop(job, None)  # bersihkan dari memori setelah diambil
    return JSONResponse(j)


# =============================================================
# KELOLA DATA AWE  Tahap 1: TARIK ke staging (penyimpanan sementara)
#   - kredensial pegawai TIDAK disimpan
#   - dedup by sid lintas tarikan & lintas pengguna
#   - TIDAK menganalisis (pemrosesan bahasa dilakukan di Tahap 2)
# =============================================================
def _awe_stage_worker(job_id, date_from, date_to, username, password, base_url, pulled_by):
    logs = []
    def prog(m):
        logs.append(m)
        _awe_job_set(job_id, message=m, log=logs[-10:])
    try:
        _awe_job_set(job_id, status="login", message="Login ke Avaya WFO")
        client = avc.AvayaClient(base_url=(base_url or None))
        client.login(username, password)
        username = None
        password = None
        _awe_job_set(job_id, status="pull", message="Menarik data ke penyimpanan sementara")
        convs = client.pull_range(date_from, date_to, on_prog=prog)
        batch_id = _uuid.uuid4().hex[:12]
        def _save():
            conn = avdb.init_db(avdb.connect())
            try:
                res = avdb.stage_upsert_convs(conn, convs, batch_id=batch_id, pulled_by=pulled_by)
                avdb.stage_mark_days(conn, convs, date_from, date_to, batch_id=batch_id, pulled_by=pulled_by)
                avdb.stage_add_batch(conn, batch_id, date_from, date_to, res["seen"], res["new"], pulled_by)
                return res
            finally:
                conn.close()
        res = _save()
        _awe_job_set(job_id, status="done", finished=True, ok=True,
                     n_conv=len(convs), n_new=res["new"], n_dup=res["dup"],
                     message="Berhasil menarik %d percakapan (%d baru, %d duplikat) ke penyimpanan sementara." % (len(convs), res["new"], res["dup"]))
    except avc.AvayaAuthError as e:
        _awe_job_set(job_id, status="error", finished=True, ok=False, need_login=True, error=str(e))
    except Exception as e:
        _awe_job_set(job_id, status="error", finished=True, ok=False, need_login=False, error=str(e))


async def awe_stage_check(request: Request):
    body = await request.json() or {}
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal (dari & sampai) wajib diisi."}, status_code=400)
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.stage_coverage_for_range(conn, df, dt)
        finally:
            conn.close()
    cov = await run_in_threadpool(_do)
    cov["ok"] = True
    cov["fully_staged"] = (len(cov.get("missing", [])) == 0)
    return JSONResponse(cov)


async def awe_stage_start(request: Request):
    body = await request.json() or {}
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    username = body.get("username") or ""
    password = body.get("password") or ""
    base_url = str(body.get("base_url") or "").strip()
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal wajib diisi."}, status_code=400)
    if not password:
        return JSONResponse({"ok": False, "error": "Password AWE wajib diisi.", "need_login": True}, status_code=400)
    me = getattr(request.state, "user", None) or {}
    job_id = _uuid.uuid4().hex
    _awe_job_set(job_id, status="queued", finished=False, ok=None, message="Menyiapkan")
    _threading.Thread(target=_awe_stage_worker,
                      args=(job_id, df, dt, username, password, base_url, me.get("username") or ""),
                      daemon=True).start()
    return JSONResponse({"ok": True, "job": job_id})


async def awe_stage_progress(job: str = ""):
    j = _awe_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "progress": j})


async def awe_stage_fetch(job: str = ""):
    j = _awe_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    if not j.get("finished"):
        return JSONResponse({"ok": True, "pending": True, "progress": j})
    with _AWE_PULL_LOCK:
        _AWE_PULL_JOBS.pop(job, None)
    return JSONResponse(j)


async def awe_stage_summary():
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return {"stats": avdb.stage_stats(conn), "batches": avdb.stage_list_batches(conn)}
        finally:
            conn.close()
    d = await run_in_threadpool(_do)
    d["ok"] = True
    return JSONResponse(d)


async def awe_stage_purge(request: Request):
    body = await request.json() or {}
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            if df and dt:
                return avdb.stage_purge(conn, df, dt)
            return avdb.stage_purge(conn)
        finally:
            conn.close()
    n = await run_in_threadpool(_do)
    return JSONResponse({"ok": True, "deleted": n})


# =============================================================
# KELOLA DATA AWE  Tahap 2: PROSES (pemrosesan bahasa) staging -> awe_runs
#   - materialisasi staging ke run pemrosesan (step12)
#   - tarik intent Dialogflow (step13) lalu analisis backend (step14)
#   - hasil disimpan ke database AWE; opsional bersihkan staging
# =============================================================
_AWE_PROC_JOBS = {}


def _proc_job_set(job_id, **kw):
    with _AWE_PULL_LOCK:
        j = _AWE_PROC_JOBS.setdefault(job_id, {})
        j.update(kw)


def _proc_job_get(job_id):
    with _AWE_PULL_LOCK:
        j = _AWE_PROC_JOBS.get(job_id)
        return dict(j) if j else {}


def _awe_process_worker(job_id, date_from, date_to, purge_after):
    import time as _time
    try:
        _proc_job_set(job_id, status="load", message="Menyiapkan data dari penyimpanan sementara")
        conn = avdb.init_db(avdb.connect())
        try:
            convs = avdb.stage_load_convs(conn, date_from or None, date_to or None)
        finally:
            conn.close()
        if not convs:
            _proc_job_set(job_id, status="error", finished=True, ok=False,
                          error="Tidak ada data di penyimpanan sementara untuk diproses.")
            return
        days = sorted({str(c.get("tanggal") or "")[:10] for c in convs if isinstance(c, dict) and c.get("tanggal")})
        d_min = days[0] if days else (str(date_from)[:10] if date_from else "")
        d_max = days[-1] if days else (str(date_to)[:10] if date_to else "")
        procrun = "awe_proc_" + _uuid.uuid4().hex[:8]
        save_artifact(CONFIG, procrun, 12, "json",
                      json.dumps({"data": convs}, ensure_ascii=False),
                      "avaya_gabungan.json",
                      {"status": "Disiapkan dari staging", "total_percakapan": len(convs)})
        st = load_state(CONFIG, procrun)
        st["awe_pull_range"] = [d_min, d_max]
        save_state(CONFIG, procrun, st)
        ctx = Ctx(procrun, {"mode": "auto"}, {}, {})
        _proc_job_set(job_id, status="intent", message="Menarik katalog intent Dialogflow")
        avaya2_pull_intents(CONFIG, ctx)
        _proc_job_set(job_id, status="analyze", message="Memproses bahasa (analisis backend)")
        started = avaya3_start(CONFIG, ctx)
        job = started.get("job_id")
        out = None
        for _i in range(2000):
            out = avaya3_fetch(CONFIG, Ctx(procrun, {}, {}, {"job": job}))
            if isinstance(out, dict) and out.get("pending"):
                info = out.get("progress") or {}
                _proc_job_set(job_id, status="analyze",
                              message="Proses bahasa: %s" % (info.get("message") or info.get("status") or "memproses"))
                _time.sleep(2)
                continue
            break
        art = (out or {}).get("artifact") or {}
        summ = art.get("summary") or {}
        saved = summ.get("disimpan_ke_database") or "Ya"
        if purge_after:
            conn = avdb.init_db(avdb.connect())
            try:
                if date_from and date_to:
                    avdb.stage_purge(conn, date_from, date_to)
                else:
                    avdb.stage_purge(conn)
            finally:
                conn.close()
        _proc_job_set(job_id, status="done", finished=True, ok=True,
                      n_conv=len(convs), rentang="%s s/d %s" % (d_min, d_max),
                      disimpan=saved, purged=bool(purge_after),
                      message="Selesai memproses %d percakapan (%s s/d %s)." % (len(convs), d_min, d_max))
    except Exception as e:
        _proc_job_set(job_id, status="error", finished=True, ok=False, error=str(e))


async def awe_process_start(request: Request):
    body = await request.json() or {}
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    purge_after = bool(body.get("purge_after"))
    def _cnt():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.stage_count(conn, df or None, dt or None)
        finally:
            conn.close()
    n = await run_in_threadpool(_cnt)
    if not n:
        return JSONResponse({"ok": False, "error": "Penyimpanan sementara kosong untuk rentang ini. Tarik data dulu."}, status_code=400)
    job_id = _uuid.uuid4().hex
    _proc_job_set(job_id, status="queued", finished=False, ok=None, message="Menyiapkan")
    _threading.Thread(target=_awe_process_worker, args=(job_id, df, dt, purge_after), daemon=True).start()
    return JSONResponse({"ok": True, "job": job_id, "n_staged": n})


async def awe_process_progress(job: str = ""):
    j = _proc_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "progress": j})


async def awe_process_fetch(job: str = ""):
    j = _proc_job_get(job)
    if not j:
        return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
    if not j.get("finished"):
        return JSONResponse({"ok": True, "pending": True, "progress": j})
    with _AWE_PULL_LOCK:
        _AWE_PROC_JOBS.pop(job, None)
    return JSONResponse(j)

def register(app, *, save_artifact, load_state, save_state, Ctx,
             avaya2_pull_intents, avaya3_start, avaya3_fetch,
             api_endpoint, curl_json_raw):
    g = globals()
    g["save_artifact"] = save_artifact
    g["load_state"] = load_state
    g["save_state"] = save_state
    g["Ctx"] = Ctx
    g["avaya2_pull_intents"] = avaya2_pull_intents
    g["avaya3_start"] = avaya3_start
    g["avaya3_fetch"] = avaya3_fetch
    g["api_endpoint"] = api_endpoint
    g["curl_json_raw"] = curl_json_raw
    app.add_api_route("/awe/kelola", awe_kelola_page, methods=["GET"])
    app.add_api_route("/awe/penilaian", awe_penilaian_page, methods=["GET"])
    app.add_api_route("/awe/deflection-gap", awe_deflection_gap_page, methods=["GET"])
    app.add_api_route("/awe", awe_page, methods=["GET"])
    app.add_api_route("/api/awe/runs", awe_list_runs, methods=["GET"])
    app.add_api_route("/api/awe/run", awe_get_run, methods=["GET"])
    app.add_api_route("/awe/dashboard", awe_run_dashboard, methods=["GET"])
    app.add_api_route("/api/awe/delete", awe_delete_run, methods=["POST"])
    app.add_api_route("/api/awe/pull/check", awe_pull_check, methods=["POST"])
    app.add_api_route("/api/awe/pull/start", awe_pull_start, methods=["POST"])
    app.add_api_route("/api/awe/pull/progress", awe_pull_progress, methods=["GET"])
    app.add_api_route("/api/awe/pull/fetch", awe_pull_fetch, methods=["GET"])
    app.add_api_route("/api/awe/stage/check", awe_stage_check, methods=["POST"])
    app.add_api_route("/api/awe/stage/start", awe_stage_start, methods=["POST"])
    app.add_api_route("/api/awe/stage/progress", awe_stage_progress, methods=["GET"])
    app.add_api_route("/api/awe/stage/fetch", awe_stage_fetch, methods=["GET"])
    app.add_api_route("/api/awe/stage/summary", awe_stage_summary, methods=["GET"])
    app.add_api_route("/api/awe/stage/purge", awe_stage_purge, methods=["POST"])
    app.add_api_route("/api/awe/process/start", awe_process_start, methods=["POST"])
    app.add_api_route("/api/awe/process/progress", awe_process_progress, methods=["GET"])
    app.add_api_route("/api/awe/process/fetch", awe_process_fetch, methods=["GET"])
