# -*- coding: utf-8 -*-
"""awe/phone_routes.py - halaman telepon + endpoint aksi Phone (dispatch).

TERPISAH total dari alur Chat. Handler didaftarkan oleh awe/routes.py
(register()). Semua aksi lewat POST /api/awe/phone/probe (body.action):
  UJI increment:
    - search  : uji pencarian Phone (Increment 1), sampel baris.
    - media   : locator + unduh 1 audio DASH (2a/2b) + opsional STT (stt=true).
  MENU Kelola Data Phone (Fase 3+):
    - pull_start    : TARIK harian ke DB (async, butuh login). -> {job}
    - analyze_start : STT+LLM baris pending (async, tanpa login). -> {job}
    - job_progress  : status job berjalan (butuh job).
    - job_fetch     : status akhir job lalu buang dari memori (butuh job).
    - coverage      : ringkasan per hari (audio/transkrip/analisis) + stats.
    - list          : daftar interaksi (opsional rentang tanggal).
    - detail        : satu interaksi lengkap (butuh sid).
Butuh izin 'awe_manage'. Kredensial dipakai sekali lalu dilupakan (tak ke disk/DB).
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page
import db.users_db as usr
import avaya.client as avc
import avaya.phone as avphone
import avaya.phone_stt as avstt

# Fail-soft: kalau modul job bermasalah, halaman uji + search/media tetap jalan.
try:
    import awe.phone_jobs as pjobs
except Exception as _pjobs_exc:
    pjobs = None
    print("[AWE-PHONE] modul job telepon dilewati:", _pjobs_exc, flush=True)

_MENU_ACTIONS = ("pull_start", "analyze_start", "job_progress", "job_fetch",
                 "coverage", "list", "detail")


async def awe_telepon_page(request: Request):
    return render_page(request, "awe_telepon.html", "awe_telepon")


async def awe_phone_probe(request: Request):
    """Dispatch aksi Phone (login-then-forget; kredensial tidak disimpan)."""
    user = getattr(request.state, "user", None) or {}
    if not usr.area_allowed(user.get("role"), "awe_manage"):
        return JSONResponse({"ok": False, "error": "Akses ditolak untuk peran Anda."}, status_code=403)
    try:
        body = await request.json() or {}
    except Exception:
        body = {}
    action = str(body.get("action") or "search").strip().lower()
    df = str(body.get("date_from") or "").strip()
    dt = str(body.get("date_to") or "").strip()
    username = body.get("username") or ""
    password = body.get("password") or ""
    base_url = str(body.get("base_url") or "").strip()
    itype = str(body.get("interaction_type") or "1").strip() or "1"
    limit_rows = body.get("limit_rows") or 25
    do_stt = bool(body.get("stt", False))
    stt_lang = str(body.get("stt_lang") or "id").strip() or "id"

    if action in _MENU_ACTIONS and pjobs is None:
        return JSONResponse({"ok": False, "error": "Modul job telepon tidak tersedia."}, status_code=500)

    # ---- Aksi baca / job tanpa kredensial ----
    if action in ("job_progress", "job_fetch"):
        job = str(body.get("job") or "").strip()
        if not job:
            return JSONResponse({"ok": False, "error": "job wajib diisi."}, status_code=400)
        if action == "job_progress":
            j = pjobs.job_get(job)
            if not j:
                return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
            return JSONResponse({"ok": True, "progress": j})
        j = pjobs.job_fetch(job)
        if j is None:
            return JSONResponse({"ok": False, "error": "Job tidak ditemukan."}, status_code=404)
        if j.get("pending"):
            return JSONResponse({"ok": True, "pending": True, "progress": j.get("progress")})
        j["ok"] = bool(j.get("ok"))
        return JSONResponse(j)

    if action == "coverage":
        try:
            d = await run_in_threadpool(pjobs.coverage, df or None, dt or None)
            d["ok"] = True
            return JSONResponse(d)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    if action == "list":
        try:
            lim = int(body.get("limit_rows") or 200)
            d = await run_in_threadpool(pjobs.list_rows, df or None, dt or None, lim)
            d["ok"] = True
            return JSONResponse(d)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    if action == "detail":
        sid = str(body.get("sid") or "").strip()
        if not sid:
            return JSONResponse({"ok": False, "error": "sid wajib diisi."}, status_code=400)
        try:
            d = await run_in_threadpool(pjobs.detail, sid)
            if not d:
                return JSONResponse({"ok": False, "error": "Interaksi tidak ditemukan."}, status_code=404)
            return JSONResponse({"ok": True, "interaction": d})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    if action == "analyze_start":
        day = str(body.get("day") or df or "").strip()
        try:
            job = pjobs.start_analyze(day=day, limit=int(limit_rows or 25),
                                      min_durasi=int(body.get("min_durasi") or 3))
            return JSONResponse({"ok": True, "job": job})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    # ---- Aksi yang butuh tanggal + kredensial ----
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal (dari & sampai) wajib diisi."}, status_code=400)
    if not password:
        return JSONResponse({"ok": False, "error": "Password AWE wajib diisi.", "need_login": True}, status_code=400)

    if action == "pull_start":
        try:
            job = pjobs.start_pull(df, dt, username, password, base_url,
                                   limit=int(limit_rows or 25),
                                   pulled_by=(user.get("username") or ""))
            return JSONResponse({"ok": True, "job": job})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    if action == "media":
        def _do_media():
            client = avphone.AvayaPhoneClient(base_url=(base_url or None))
            client.login(username, password)
            res = client.probe_media(df, dt)
            if do_stt:
                try:
                    avstt.attach_transcript(res, lang=stt_lang)
                except Exception as e:
                    res.setdefault("media_summary", []).append(
                        {"item": "STT", "http": "GAGAL", "locator_status": "",
                         "encryption": "", "detail": "ERROR: " + str(e)[:140]})
            return res
        try:
            res = await run_in_threadpool(_do_media)
            res["ok"] = True
            return JSONResponse(res)
        except avc.AvayaAuthError as e:
            return JSONResponse({"ok": False, "need_login": True, "error": str(e)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    def _do():
        client = avphone.AvayaPhoneClient(base_url=(base_url or None))
        client.login(username, password)
        return client.probe_search(df, dt, interaction_type=itype, limit_rows=limit_rows)

    try:
        res = await run_in_threadpool(_do)
        res["ok"] = True
        return JSONResponse(res)
    except avc.AvayaAuthError as e:
        return JSONResponse({"ok": False, "need_login": True, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
