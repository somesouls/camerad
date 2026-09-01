# -*- coding: utf-8 -*-
"""awe/phone_autopull.py - Auto-pull harian AWE Telepon (mirror auto-pull Livechat).

Tarik interaksi Telepon H-1 otomatis (Tahap 1: tarik + unduh audio) memakai
worker yang SAMA dengan alur manual Kelola Data Phone (awe.phone_jobs), lalu
OPSIONAL menjalankan Tahap 2 (STT Qwen + analisis LLM) bila AWE_PHONE_INGEST_ANALYZE
aktif. Kredensial dari .env (AVAYA_USERNAME/PASSWORD/BASE_URL) - login-then-forget,
tidak pernah disimpan. Chat & Livechat tidak tersentuh.

Anti-tumpang-tindih via lock non-blok; status terakhir ditulis ke berkas JSON
kecil (phone_autopull_status.json) agar tampil di UI & bertahan setelah restart.

Env:
  AWE_PHONE_SCHEDULER=1        aktifkan penjadwal harian (default 0/mati)
  AWE_PHONE_INGEST_HOUR=6      jam cron (Asia/Jakarta)
  AWE_PHONE_INGEST_MINUTE=0    menit cron
  AWE_PHONE_PULL_LIMIT=25      cap tarik per hari
  AWE_PHONE_INGEST_ANALYZE=0   jalankan Tahap 2 STT+LLM setelah tarik (LAMBAT)
  AWE_PHONE_ANALYZE_MINDUR=3   durasi min (dtk) utk analisis
  AWE_PHONE_ANALYZE_LIMIT      batas baris analisis (default = AWE_PHONE_PULL_LIMIT)
Rute didaftarkan via register_app(): GET /api/awe/phone/autopull/status,
POST /api/awe/phone/autopull/now. Penjadwal via maybe_start_scheduler().
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
    return os.path.join(base, "phone_autopull_status.json")


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


def _yesterday():
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Jakarta")
    except Exception:
        tz = None
    now = _dt.datetime.now(tz) if tz else _dt.datetime.now()
    d = now.date() - _dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _env_flag(name, default="0"):
    v = (os.environ.get(name, default) or default).strip().lower()
    return v not in ("0", "", "false", "no", "off")


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except Exception:
        return int(default)


def phone_autopull_run(date_from=None, date_to=None, limit=None,
                       do_analyze=None, trigger="scheduler"):
    """Tarik (+opsional analisis) Telepon utk rentang (default H-1). Kembalikan status."""
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "skipped": True,
                "error": "Auto-pull telepon lain sedang berjalan; dilewati."}
    if not date_from or not date_to:
        y = _yesterday()
        date_from = date_from or y
        date_to = date_to or y
    if limit is None:
        limit = _int_env("AWE_PHONE_PULL_LIMIT", 25)
    if do_analyze is None:
        do_analyze = _env_flag("AWE_PHONE_INGEST_ANALYZE", "0")
    status = {"trigger": trigger, "kind": "phone",
              "started_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "range": "%s s/d %s" % (date_from, date_to), "limit": int(limit)}
    try:
        if not (os.environ.get("AVAYA_PASSWORD") or "").strip():
            raise RuntimeError(
                "Kredensial AWE belum diset. Isi AVAYA_USERNAME & AVAYA_PASSWORD di .env.")
        import awe.phone_jobs as pj
        pres = pj.pull_sync(date_from, date_to, limit=int(limit),
                            pulled_by="auto:" + (trigger or "scheduler"))
        status["stage_ok"] = bool(pres.get("ok"))
        status["staged"] = pres.get("staged")
        status["with_audio"] = pres.get("with_audio")
        if not pres.get("ok"):
            status["ok"] = False
            status["need_login"] = bool(pres.get("need_login"))
            status["error"] = pres.get("error") or "Penarikan gagal."
            return status
        if not (pres.get("staged") or 0):
            status["ok"] = True
            status["message"] = "Tidak ada interaksi telepon pada rentang %s." % status["range"]
            return status
        if do_analyze:
            mind = _int_env("AWE_PHONE_ANALYZE_MINDUR", 3)
            alim = _int_env("AWE_PHONE_ANALYZE_LIMIT", int(limit))
            ares = pj.analyze_sync(day=(date_from if date_from == date_to else ""),
                                   limit=alim, min_durasi=mind)
            status["analyze_ok"] = bool(ares.get("ok"))
            status["stt_ok"] = ares.get("stt_ok")
            status["llm_ok"] = ares.get("llm_ok")
            if not ares.get("ok"):
                status["ok"] = False
                status["error"] = ares.get("error") or "Analisis gagal."
                return status
        status["ok"] = True
        status["message"] = "Berhasil menarik %s interaksi (%s dengan audio)%s." % (
            pres.get("staged"), pres.get("with_audio"),
            " lalu dianalisis (STT+LLM)" if do_analyze else
            " (analisis STT+LLM belum dijalankan)")
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
        "enabled": _env_flag("AWE_PHONE_SCHEDULER", "0"),
        "configured": bool((os.environ.get("AVAYA_PASSWORD") or "").strip()),
        "hour": os.environ.get("AWE_PHONE_INGEST_HOUR", "6"),
        "minute": os.environ.get("AWE_PHONE_INGEST_MINUTE", "0"),
        "limit": _int_env("AWE_PHONE_PULL_LIMIT", 25),
        "analyze": _env_flag("AWE_PHONE_INGEST_ANALYZE", "0"),
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
        return JSONResponse({"ok": False, "error": "Auto-pull telepon sedang berjalan."}, status_code=409)
    if not (os.environ.get("AVAYA_PASSWORD") or "").strip():
        return JSONResponse({"ok": False, "need_login": True,
                             "error": "Kredensial AWE belum diset di .env (AVAYA_USERNAME/AVAYA_PASSWORD)."}, status_code=400)
    _threading.Thread(
        target=lambda: phone_autopull_run(date_from=df, date_to=dt, trigger="manual"),
        daemon=True).start()
    return JSONResponse({"ok": True, "started": True,
                         "message": "Tarik otomatis telepon dimulai di latar belakang. Status diperbarui otomatis."})


def register_app():
    """Daftarkan rute auto-pull telepon ke app (idempoten)."""
    global _ROUTES_DONE
    if _ROUTES_DONE:
        return
    from app_core import app
    app.add_api_route("/api/awe/phone/autopull/status", autopull_status, methods=["GET"])
    app.add_api_route("/api/awe/phone/autopull/now", autopull_now, methods=["POST"])
    _ROUTES_DONE = True


def maybe_start_scheduler():
    """Mulai penjadwal harian bila AWE_PHONE_SCHEDULER=1 & kredensial ada (idempoten)."""
    global _SCHED
    if _SCHED is not None:
        return _SCHED
    if not _env_flag("AWE_PHONE_SCHEDULER", "0"):
        print("[awe-phone-scheduler] nonaktif (set AWE_PHONE_SCHEDULER=1 utk mengaktifkan).", flush=True)
        return None
    if not (os.environ.get("AVAYA_PASSWORD") or "").strip():
        print("[awe-phone-scheduler] AVAYA_PASSWORD kosong; penjadwal dilewati.", flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print("[awe-phone-scheduler] APScheduler belum terpasang:", e, flush=True)
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Jakarta")
    except Exception:
        tz = None
    hour = _int_env("AWE_PHONE_INGEST_HOUR", 6)
    minute = _int_env("AWE_PHONE_INGEST_MINUTE", 0)

    def _job():
        try:
            res = phone_autopull_run(trigger="scheduler")
            print("[awe-phone-scheduler] auto-pull selesai:",
                  (res or {}).get("message") or (res or {}).get("error"), flush=True)
        except Exception as e:
            print("[awe-phone-scheduler] auto-pull gagal:", e, flush=True)

    sch = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
    sch.add_job(_job, "cron", hour=hour, minute=minute, id="daily_awe_phone_ingest",
                replace_existing=True, max_instances=1, coalesce=True)
    sch.start()
    _SCHED = sch
    print("[awe-phone-scheduler] tarik AWE Telepon harian aktif jam %02d:%02d Asia/Jakarta." % (hour, minute),
          flush=True)
    return sch
