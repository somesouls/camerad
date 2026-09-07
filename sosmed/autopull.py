# -*- coding: utf-8 -*-
"""sosmed/autopull.py — Auto-pull harian Sosmed tanpa ekstensi (Fase 1 + Fase 2).

Generalisasi 3-platform (X / Instagram / TikTok). Untuk tiap platform: menarik
percakapan/komentar H-1 otomatis tiap hari lewat kolektor browser headless
(sosmed/x_collector.py, sosmed/ig_collector.py, sosmed/tiktok_collector.py),
lalu meng-ingest ke sosmed.db (pairing Q&A sadar-thread) dan MENANDAI hari itu
\"sudah ditarik\" di sosmed/pull_log.py — TERLEPAS dari jumlah item (penanda hari
TIDAK disimpulkan dari created_at).

Anti-tumpang-tindih via lock non-blok per-platform; status terakhir per platform
ditulis ke berkas JSON kecil (sosmed_<platform>_autopull_status.json) agar tampil
di UI & bertahan setelah restart.

CATATAN IG/TikTok: kedua platform tak punya pencarian per-tanggal publik, jadi
kolektor menarik komentar pada N postingan/video TERBARU akun resmi; dedup
(UNIQUE platform+external_id) + pull_log menjaga komentar baru terkumpul tiap
hari tanpa dobel.

Env per platform (X = SOSMED_X_*, IG = SOSMED_IG_*, TikTok = SOSMED_TT_*):
  SOSMED_X_SCHEDULER=1        aktifkan penjadwal harian (default 0/mati)
  SOSMED_X_INGEST_HOUR=3      jam cron
  SOSMED_X_INGEST_MINUTE=30   menit cron
  SOSMED_X_TZ=Asia/Jakarta    zona waktu cron
  SOSMED_X_TARGET=kring_pajak handle akun resmi yang dipantau
  (IG default menit 40, TikTok default menit 50; TZ/target IG&TikTok jatuh balik
   ke nilai X bila env khususnya kosong. Kredensial/sesi & opsi collector: lihat
   masing-masing modul kolektor.)

Rute (didaftarkan via register_app(); prefix '/api/sosmed/pull-<p>-auto/' sengaja
dipakai agar tergolong area 'awe_manage' + aksi 'ingest' di app_core, sama seperti
/api/sosmed/pull-x) untuk p ∈ {x, ig, tiktok}:
  GET  /api/sosmed/pull-<p>-auto/status
  POST /api/sosmed/pull-<p>-auto/now
  GET  /api/sosmed/pull-<p>-auto/log
Penjadwal via maybe_start_scheduler().
"""
import os
import json
import importlib
import threading as _threading
import datetime as _dt

from fastapi import Request

# --------------------------------------------------------------------------
# Konfigurasi per-platform
# --------------------------------------------------------------------------
_PLATS = {
    "x": {
        "label": "X", "module": "sosmed.x_collector", "noun": "tweet",
        "source": "autopull_x", "use_target_arg": False,
        "tz_env": "SOSMED_X_TZ", "sched_env": "SOSMED_X_SCHEDULER",
        "hour_env": "SOSMED_X_INGEST_HOUR", "hour_def": 3,
        "min_env": "SOSMED_X_INGEST_MINUTE", "min_def": 30,
        "target_env": "SOSMED_X_TARGET", "target_def": "kring_pajak",
    },
    "ig": {
        "label": "Instagram", "module": "sosmed.ig_collector", "noun": "komentar",
        "source": "autopull_ig", "use_target_arg": True,
        "tz_env": "SOSMED_IG_TZ", "sched_env": "SOSMED_IG_SCHEDULER",
        "hour_env": "SOSMED_IG_INGEST_HOUR", "hour_def": 3,
        "min_env": "SOSMED_IG_INGEST_MINUTE", "min_def": 40,
        "target_env": "SOSMED_IG_TARGET", "target_def": "kring_pajak",
    },
    "tiktok": {
        "label": "TikTok", "module": "sosmed.tiktok_collector", "noun": "komentar",
        "source": "autopull_tiktok", "use_target_arg": True,
        "tz_env": "SOSMED_TT_TZ", "sched_env": "SOSMED_TT_SCHEDULER",
        "hour_env": "SOSMED_TT_INGEST_HOUR", "hour_def": 3,
        "min_env": "SOSMED_TT_INGEST_MINUTE", "min_def": 50,
        "target_env": "SOSMED_TT_TARGET", "target_def": "kring_pajak",
    },
}

_LOCKS = {p: _threading.Lock() for p in _PLATS}
_LAST = {p: {} for p in _PLATS}
_ROUTES_DONE = False
_SCHED = None


def _cfg(platform):
    return _PLATS.get((platform or "x").lower(), _PLATS["x"])


def _lock(platform):
    return _LOCKS.get((platform or "x").lower(), _LOCKS["x"])


# --------------------------------------------------------------------------
# Status file (per platform)
# --------------------------------------------------------------------------
def _base_dir():
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
    return base


def _status_path(platform):
    return os.path.join(_base_dir(), "sosmed_%s_autopull_status.json" % platform)


def _status_load(platform):
    if _LAST.get(platform):
        return dict(_LAST[platform])
    try:
        with open(_status_path(platform), "r", encoding="utf-8") as f:
            _LAST[platform] = json.load(f) or {}
    except Exception:
        _LAST[platform] = {}
    return dict(_LAST[platform])


def _status_save(platform, d):
    _LAST[platform] = dict(d or {})
    try:
        with open(_status_path(platform), "w", encoding="utf-8") as f:
            json.dump(_LAST[platform], f, ensure_ascii=False)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Util waktu / env
# --------------------------------------------------------------------------
def _tz_name(platform):
    cfg = _cfg(platform)
    return (os.environ.get(cfg["tz_env"])
            or os.environ.get("SOSMED_X_TZ")
            or "Asia/Jakarta")


def _tz(platform):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(_tz_name(platform))
    except Exception:
        return None


def _yesterday(platform):
    tz = _tz(platform)
    now = _dt.datetime.now(tz) if tz else _dt.datetime.now()
    return (now.date() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def _target(platform):
    cfg = _cfg(platform)
    return (os.environ.get(cfg["target_env"])
            or os.environ.get("SOSMED_X_TARGET")
            or cfg["target_def"]).strip().lstrip("@")


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


# --------------------------------------------------------------------------
# Inti auto-pull (generik untuk semua platform)
# --------------------------------------------------------------------------
def sosmed_autopull_run(platform, date_from=None, date_to=None, trigger="scheduler"):
    """Tarik percakapan/komentar utk rentang (default H-1), ingest, & tandai
    pull-log untuk satu platform. Kembalikan dict status."""
    platform = (platform or "x").lower()
    cfg = _cfg(platform)
    lock = _lock(platform)
    if not lock.acquire(blocking=False):
        return {"ok": False, "skipped": True,
                "error": "Auto-pull %s lain sedang berjalan; dilewati." % cfg["label"]}
    y = _yesterday(platform)
    date_from = date_from or y
    date_to = date_to or y
    status = {"trigger": trigger, "kind": platform,
              "started_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "range": "%s s/d %s" % (date_from, date_to)}
    try:
        coll = importlib.import_module(cfg["module"])
        import sosmed.db as sdb
        import sosmed.pull_log as spl
        off = sdb.official_handles()
        kwargs = {"official_handles": off, "trigger": trigger}
        if cfg["use_target_arg"]:
            kwargs["target"] = _target(platform)
        items, cinfo = coll.collect_range(date_from, date_to, **kwargs)
        status["collect_ok"] = bool(cinfo.get("ok"))
        status["fetched"] = cinfo.get("count", len(items))
        status["search_url"] = cinfo.get("url")
        if not cinfo.get("ok"):
            status["ok"] = False
            status["need_login"] = bool(cinfo.get("need_login"))
            status["need_playwright"] = bool(cinfo.get("need_playwright"))
            status["error"] = cinfo.get("error") or ("Penarikan %s gagal." % cfg["label"])
            return status
        c = sdb.connect()
        try:
            sdb.init_db(c)
            res = sdb.ingest_items(c, items, default_platform=platform,
                                   source=cfg["source"],
                                   pulled_by="auto:" + (trigger or "scheduler"))
            spl.ensure(c)
            for day in _days_in_range(date_from, date_to):
                spl.mark(c, platform, day, status="pulled",
                         n_new=res.get("n_new"), n_fetched=len(items),
                         batch_id=res.get("batch"), trigger=trigger)
        finally:
            c.close()
        status["ok"] = True
        status["n_new"] = res.get("n_new")
        status["n_dup"] = res.get("n_dup")
        status["batch"] = res.get("batch")
        status["message"] = ("Berhasil menarik %s %s (%s baru) untuk %s." %
                             (len(items), cfg["noun"], res.get("n_new"), status["range"]))
        if cinfo.get("note"):
            status["note"] = cinfo.get("note")
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
        _status_save(platform, status)
        try:
            lock.release()
        except Exception:
            pass


def sosmed_x_autopull_run(date_from=None, date_to=None, trigger="scheduler"):
    """Alias kompatibilitas-mundur untuk platform X."""
    return sosmed_autopull_run("x", date_from=date_from, date_to=date_to, trigger=trigger)


# --------------------------------------------------------------------------
# Handler rute (factory per-platform)
# --------------------------------------------------------------------------
def _make_status_handler(platform):
    cfg = _cfg(platform)

    async def _handler():
        from starlette.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        d = await run_in_threadpool(_status_load, platform)
        return JSONResponse({
            "ok": True,
            "platform": platform,
            "running": _lock(platform).locked(),
            "enabled": _env_flag(cfg["sched_env"], "0"),
            "hour": os.environ.get(cfg["hour_env"], str(cfg["hour_def"])),
            "minute": os.environ.get(cfg["min_env"], str(cfg["min_def"])),
            "tz": _tz_name(platform),
            "target": _target(platform),
            "last": d,
        })
    return _handler


def _make_now_handler(platform):
    cfg = _cfg(platform)

    async def _handler(request: Request):
        from fastapi.responses import JSONResponse
        try:
            body = await request.json() or {}
        except Exception:
            body = {}
        df = str(body.get("date_from") or "").strip() or None
        dt = str(body.get("date_to") or "").strip() or None
        if _lock(platform).locked():
            return JSONResponse(
                {"ok": False, "error": "Auto-pull %s sedang berjalan." % cfg["label"]},
                status_code=409)
        _threading.Thread(
            target=lambda: sosmed_autopull_run(platform, date_from=df, date_to=dt,
                                               trigger="manual"),
            daemon=True).start()
        return JSONResponse({"ok": True, "started": True,
                             "message": ("Penarikan %s dimulai di latar belakang. "
                                         "Status diperbarui otomatis." % cfg["label"])})
    return _handler


def _make_log_handler(platform):
    async def _handler(request: Request):
        from starlette.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        q = request.query_params
        plat = (q.get("platform") or platform).strip().lower() or platform
        start = (q.get("start") or "").strip()
        end = (q.get("end") or "").strip()

        def _do():
            import sosmed.db as sdb
            import sosmed.pull_log as spl
            c = sdb.connect()
            try:
                sdb.init_db(c)
                spl.ensure(c)
                return {"ok": True, "platform": plat,
                        "days": spl.list_days(c, platform=plat, start=start, end=end)}
            finally:
                c.close()
        return JSONResponse(await run_in_threadpool(_do))
    return _handler


# Kompatibilitas-mundur: handler X level-modul (dipakai kode/impor lama).
autopull_status = _make_status_handler("x")
autopull_now = _make_now_handler("x")
autopull_log = _make_log_handler("x")


def register_app(app=None):
    global _ROUTES_DONE
    if _ROUTES_DONE:
        return
    if app is None:
        from app_core import app as _app
        app = _app
    for platform in _PLATS:
        pref = "/api/sosmed/pull-%s-auto" % platform
        app.add_api_route(pref + "/status", _make_status_handler(platform), methods=["GET"])
        app.add_api_route(pref + "/now", _make_now_handler(platform), methods=["POST"])
        app.add_api_route(pref + "/log", _make_log_handler(platform), methods=["GET"])
    _ROUTES_DONE = True


def maybe_start_scheduler():
    """Mulai penjadwal cron harian untuk tiap platform yang diaktifkan."""
    global _SCHED
    if _SCHED is not None:
        return _SCHED
    enabled = [p for p in _PLATS if _env_flag(_cfg(p)["sched_env"], "0")]
    if not enabled:
        print("[sosmed-scheduler] nonaktif (set SOSMED_X_SCHEDULER / "
              "SOSMED_IG_SCHEDULER / SOSMED_TT_SCHEDULER = 1 utk mengaktifkan).",
              flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print("[sosmed-scheduler] APScheduler belum terpasang:", e, flush=True)
        return None

    sch = None
    for platform in enabled:
        cfg = _cfg(platform)
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(_tz_name(platform))
        except Exception:
            tz = None
        if sch is None:
            sch = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
        hour = _int_env(cfg["hour_env"], cfg["hour_def"])
        minute = _int_env(cfg["min_env"], cfg["min_def"])

        def _job(p=platform):
            try:
                res = sosmed_autopull_run(p, trigger="scheduler")
                print("[sosmed-scheduler:%s] auto-pull selesai:" % p,
                      (res or {}).get("message") or (res or {}).get("error"), flush=True)
            except Exception as e:
                print("[sosmed-scheduler:%s] auto-pull gagal:" % p, e, flush=True)

        sch.add_job(_job, "cron", hour=hour, minute=minute,
                    id="daily_sosmed_%s_ingest" % platform,
                    replace_existing=True, max_instances=1, coalesce=True)
        print("[sosmed-scheduler:%s] tarik %s harian aktif jam %02d:%02d %s."
              % (platform, cfg["label"], hour, minute, _tz_name(platform)), flush=True)

    if sch is not None:
        sch.start()
        _SCHED = sch
    return sch
