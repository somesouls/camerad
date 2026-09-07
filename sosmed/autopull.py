# -*- coding: utf-8 -*-
"""sosmed/autopull.py — Auto-pull harian X (Sosmed) tanpa ekstensi (Fase 1).

Meniru pola awe/phone_autopull.py: menarik mention X (to:<target>) untuk H-1
otomatis tiap hari memakai collector browser headless (sosmed/x_collector.py),
lalu meng-ingest ke sosmed.db (pairing Q&A sadar-thread) dan MENANDAI hari itu
\"sudah ditarik\" di sosmed/pull_log.py — TERLEPAS dari jumlah item (menjawab isu
\"hasil bisa sampai induk\": penanda hari TIDAK disimpulkan dari created_at).

Anti-tumpang-tindih via lock non-blok; status terakhir ditulis ke berkas JSON
kecil (sosmed_x_autopull_status.json) agar tampil di UI & bertahan setelah restart.

Env:
  SOSMED_X_SCHEDULER=1        aktifkan penjadwal harian (default 0/mati)
  SOSMED_X_INGEST_HOUR=3      jam cron (Asia/Jakarta)
  SOSMED_X_INGEST_MINUTE=30   menit cron
  (kredensial/sesi & opsi collector: lihat sosmed/x_collector.py)

Rute (didaftarkan via register_app(); prefix '/api/sosmed/pull-x-auto/' sengaja
dipakai agar tergolong area 'awe_manage' + aksi 'ingest' di app_core, sama
seperti /api/sosmed/pull-x):
  GET  /api/sosmed/pull-x-auto/status
  POST /api/sosmed/pull-x-auto/now
  GET  /api/sosmed/pull-x-auto/log
Penjadwal via maybe_start_scheduler().
"""
import os
import json
import threading as _threading
import datetime as _dt

from fastapi import Request

_LOCK = _threading.Lock()
_LAST = {}
_ROUTES_DONE = False
_SCHED = None


def _status_path():
    base = ""
    try:
        from app_core import CONFIG
        base = CONFIG.get("runs_dir") or ""
    except Exception:
        base = ""
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "sosmed_x_autopull_status.json")


def _status_load():
    global _LAST
    if _LAST:
        return dict(_LAST)
    try:
        with open(_status_path(), "r", encoding="utf-8") as f:
            _LAST = json.load(f) or {}
    except Exception:
        _LAST = {}
    return dict(_LAST)


def _status_save(d):
    global _LAST
    _LAST = dict(d or {})
    try:
        with open(_status_path(), "w", encoding="utf-8") as f:
            json.dump(_LAST, f, ensure_ascii=False)
    except Exception:
        pass


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.environ.get("SOSMED_X_TZ", "Asia/Jakarta"))
    except Exception:
        return None


def _yesterday():
    tz = _tz()
    now = _dt.datetime.now(tz) if tz else _dt.datetime.now()
    return (now.date() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _days_in_range(a, b):
    try:
        da = _dt.datetime.strptime(a, "%Y-%m-%d").date()
        db = _dt.datetime.strptime(b, "%Y-%m-%d").date()
    except Exception:
        return [a]
    out = []
    while da <= db:
        out.append(da.strftime("%Y-%m-%d"))
        da += _dt.timedelta(days=1)
    return out


def _env_flag(name, default="0"):
    v = (os.environ.get(name, default) or default).strip().lower()
    return v not in ("0", "", "false", "no", "off")


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except Exception:
        return int(default)


def sosmed_x_autopull_run(date_from=None, date_to=None, trigger="scheduler"):
    """Tarik mention X utk rentang (default H-1), ingest, & tandai pull-log."""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "skipped": True,
                "error": "Auto-pull X lain sedang berjalan; dilewati."}
    y = _yesterday()
    date_from = date_from or y
    date_to = date_to or y
    status = {"trigger": trigger, "kind": "x",
              "started_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "range": "%s s/d %s" % (date_from, date_to)}
    try:
        import sosmed.x_collector as xc
        import sosmed.db as sdb
        import sosmed.pull_log as spl
        off = sdb.official_handles()
        items, cinfo = xc.collect_range(date_from, date_to,
                                        official_handles=off, trigger=trigger)
        status["collect_ok"] = bool(cinfo.get("ok"))
        status["fetched"] = cinfo.get("count", len(items))
        status["search_url"] = cinfo.get("url")
        if not cinfo.get("ok"):
            status["ok"] = False
            status["need_login"] = bool(cinfo.get("need_login"))
            status["need_playwright"] = bool(cinfo.get("need_playwright"))
            status["error"] = cinfo.get("error") or "Penarikan X gagal."
            return status
        c = sdb.connect()
        try:
            sdb.init_db(c)
            res = sdb.ingest_items(c, items, default_platform="x",
                                   source="autopull_x",
                                   pulled_by="auto:" + (trigger or "scheduler"))
            spl.ensure(c)
            for day in _days_in_range(date_from, date_to):
                spl.mark(c, "x", day, status="pulled",
                         n_new=res.get("n_new"), n_fetched=len(items),
                         batch_id=res.get("batch"), trigger=trigger)
        finally:
            c.close()
        status["ok"] = True
        status["n_new"] = res.get("n_new")
        status["n_dup"] = res.get("n_dup")
        status["batch"] = res.get("batch")
        status["message"] = ("Berhasil menarik %s tweet (%s baru) untuk %s." %
                             (len(items), res.get("n_new"), status["range"]))
        try:
            import sosmed.routes as _sr
            _sr._kick_reindex_bg()
        except Exception:
            pass
        return status
    except Exception as e:
        status["ok"] = False
        status["error"] = str(e)
        return status
    finally:
        status["finished_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _status_save(status)
        try:
            _LOCK.release()
        except Exception:
            pass


async def autopull_status():
    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse
    d = await run_in_threadpool(_status_load)
    return JSONResponse({
        "ok": True,
        "running": _LOCK.locked(),
        "enabled": _env_flag("SOSMED_X_SCHEDULER", "0"),
        "hour": os.environ.get("SOSMED_X_INGEST_HOUR", "3"),
        "minute": os.environ.get("SOSMED_X_INGEST_MINUTE", "30"),
        "tz": os.environ.get("SOSMED_X_TZ", "Asia/Jakarta"),
        "target": os.environ.get("SOSMED_X_TARGET", "kring_pajak"),
        "last": d,
    })


async def autopull_now(request: Request):
    from fastapi.responses import JSONResponse
    try:
        body = await request.json() or {}
    except Exception:
        body = {}
    df = str(body.get("date_from") or "").strip() or None
    dt = str(body.get("date_to") or "").strip() or None
    if _LOCK.locked():
        return JSONResponse({"ok": False, "error": "Auto-pull X sedang berjalan."},
                            status_code=409)
    _threading.Thread(
        target=lambda: sosmed_x_autopull_run(date_from=df, date_to=dt, trigger="manual"),
        daemon=True).start()
    return JSONResponse({"ok": True, "started": True,
                         "message": "Penarikan X dimulai di latar belakang. Status diperbarui otomatis."})


async def autopull_log(request: Request):
    from starlette.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse
    q = request.query_params
    platform = (q.get("platform") or "x").strip().lower() or "x"
    start = (q.get("start") or "").strip()
    end = (q.get("end") or "").strip()

    def _do():
        import sosmed.db as sdb
        import sosmed.pull_log as spl
        c = sdb.connect()
        try:
            sdb.init_db(c)
            spl.ensure(c)
            return {"ok": True, "platform": platform,
                    "days": spl.list_days(c, platform=platform, start=start, end=end)}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


def register_app(app=None):
    global _ROUTES_DONE
    if _ROUTES_DONE:
        return
    if app is None:
        from app_core import app as _app
        app = _app
    app.add_api_route("/api/sosmed/pull-x-auto/status", autopull_status, methods=["GET"])
    app.add_api_route("/api/sosmed/pull-x-auto/now", autopull_now, methods=["POST"])
    app.add_api_route("/api/sosmed/pull-x-auto/log", autopull_log, methods=["GET"])
    _ROUTES_DONE = True


def maybe_start_scheduler():
    global _SCHED
    if _SCHED is not None:
        return _SCHED
    if not _env_flag("SOSMED_X_SCHEDULER", "0"):
        print("[sosmed-x-scheduler] nonaktif (set SOSMED_X_SCHEDULER=1 utk mengaktifkan).", flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print("[sosmed-x-scheduler] APScheduler belum terpasang:", e, flush=True)
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.environ.get("SOSMED_X_TZ", "Asia/Jakarta"))
    except Exception:
        tz = None
    hour = _int_env("SOSMED_X_INGEST_HOUR", 3)
    minute = _int_env("SOSMED_X_INGEST_MINUTE", 30)

    def _job():
        try:
            res = sosmed_x_autopull_run(trigger="scheduler")
            print("[sosmed-x-scheduler] auto-pull selesai:",
                  (res or {}).get("message") or (res or {}).get("error"), flush=True)
        except Exception as e:
            print("[sosmed-x-scheduler] auto-pull gagal:", e, flush=True)

    sch = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
    sch.add_job(_job, "cron", hour=hour, minute=minute, id="daily_sosmed_x_ingest",
                replace_existing=True, max_instances=1, coalesce=True)
    sch.start()
    _SCHED = sch
    print("[sosmed-x-scheduler] tarik X harian aktif jam %02d:%02d %s."
          % (hour, minute, os.environ.get("SOSMED_X_TZ", "Asia/Jakarta")), flush=True)
    return sch
