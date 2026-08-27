# -*- coding: utf-8 -*-
"""awe/phone_routes.py — halaman uji telepon + endpoint probe.

TERPISAH total dari alur Chat. Handler didaftarkan oleh awe/routes.py
(register()) memakai objek app yang sama dengan route AWE lain. Endpoint
menguji pencarian Phone (action="search") atau mengambil locator audio via
GetMedia (action="media", Increment 2a) lalu menampilkan hasil MENTAH untuk
verifikasi; tidak menyimpan apa pun & tidak mengunduh audio. Butuh izin
'awe_manage' (sama seperti Kelola Data AWE).
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page
import db.users_db as usr
import avaya.client as avc
import avaya.phone as avphone


async def awe_telepon_page(request: Request):
    return render_page(request, "awe_telepon.html", "awe_telepon")


async def awe_phone_probe(request: Request):
    """Uji pencarian Phone / locator audio (login-then-forget). Kredensial
    tidak disimpan."""
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
    if not df or not dt:
        return JSONResponse({"ok": False, "error": "Tanggal (dari & sampai) wajib diisi."}, status_code=400)
    if not password:
        return JSONResponse({"ok": False, "error": "Password AWE wajib diisi.", "need_login": True}, status_code=400)

    if action == "media":
        def _do_media():
            client = avphone.AvayaPhoneClient(base_url=(base_url or None))
            client.login(username, password)
            return client.probe_media(df, dt)
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
