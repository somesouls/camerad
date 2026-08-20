# -*- coding: utf-8 -*-
"""auth_routes.py — Rute autentikasi & manajemen user (migrasi langkah 2).

Dipisah dari web_app.py. Daftarkan dengan:
    import auth_routes; auth_routes.register(app)

Modul ini meng-import fondasi dari app_core (bukan dari web_app) agar tidak
terjadi circular import.
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

import db.users_db as usr
from app_core import render_page, _load_html


def register(app):
    @app.get("/login")
    async def login_page():
        return HTMLResponse(_load_html("login.html"))

    @app.get("/credit")
    async def credit_page():
        # Halaman kredit & penghargaan (publik; lihat _PUBLIC_PATHS di app_core).
        return HTMLResponse(_load_html("credit.html"))

    @app.get("/users")
    async def users_page(request: Request):
        return render_page(request, "users.html", "users")

    @app.get("/profil")
    async def profil_page(request: Request):
        # Halaman "Akun Saya" untuk semua peran (ganti sandi + foto avatar).
        return render_page(request, "profil.html", "profil")

    @app.post("/api/login")
    async def api_login(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                u = usr.authenticate(c, username, password)
                if not u:
                    return None
                return {"user": u, "token": usr.create_session(c, u["id"])}
            finally:
                c.close()

        res = await run_in_threadpool(_run)
        if not res:
            return JSONResponse({"ok": False, "error": "Username atau sandi salah, atau akun nonaktif."}, status_code=401)

        resp = JSONResponse({
            "ok": True,
            "user": {
                "username": res["user"]["username"],
                "role": res["user"]["role"],
                "nama": res["user"].get("nama", ""),
            },
        })
        resp.set_cookie("session", res["token"], httponly=True, samesite="lax", max_age=usr.session_ttl(), path="/")
        return resp

    @app.get("/api/logout")
    async def api_logout(request: Request):
        token = request.cookies.get("session")

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                usr.delete_session(c, token)
            finally:
                c.close()

        try:
            await run_in_threadpool(_run)
        except Exception:
            pass

        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie("session", path="/")
        return resp

    @app.get("/api/profil")
    async def api_profil_me(request: Request):
        me = getattr(request.state, "user", None) or {}
        if not me.get("id"):
            return JSONResponse({"ok": False, "error": "Sesi tidak valid."}, status_code=401)
        return JSONResponse({
            "ok": True,
            "user": {
                "username": me.get("username"),
                "nama": me.get("nama", ""),
                "role": me.get("role"),
                "role_label": usr.role_label(me.get("role")),
                "avatar": me.get("avatar", "") or "",
            },
        })

    @app.post("/api/profil/password")
    async def api_profil_password(request: Request):
        me = getattr(request.state, "user", None) or {}
        if not me.get("id"):
            return JSONResponse({"ok": False, "error": "Sesi tidak valid."}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        old_pw = (body.get("old_password") or "") if isinstance(body, dict) else ""
        new_pw = (body.get("new_password") or "") if isinstance(body, dict) else ""

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                return usr.change_own_password(c, int(me["id"]), old_pw, new_pw)
            finally:
                c.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/profil/avatar")
    async def api_profil_avatar(request: Request):
        me = getattr(request.state, "user", None) or {}
        if not me.get("id"):
            return JSONResponse({"ok": False, "error": "Sesi tidak valid."}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        avatar = body.get("avatar") if isinstance(body, dict) else ""
        if avatar is None:
            avatar = ""

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                return usr.set_avatar(c, int(me["id"]), avatar)
            finally:
                c.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/users")
    async def api_users_list(request: Request):
        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                return usr.list_users(c)
            finally:
                c.close()

        try:
            users = await run_in_threadpool(_run)
            me = getattr(request.state, "user", None) or {}
            return JSONResponse({
                "ok": True,
                "users": users,
                "me": {"username": me.get("username"), "role": me.get("role")},
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/users/save")
    async def api_users_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        uid = body.get("id")

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                if uid:
                    if any(k in body for k in ("nama", "role", "aktif")):
                        r = usr.update_user(c, int(uid), nama=body.get("nama"), role=body.get("role"), aktif=body.get("aktif"))
                        if not r.get("ok"):
                            return r
                    if body.get("password"):
                        r = usr.set_password(c, int(uid), body.get("password"))
                        if not r.get("ok"):
                            return r
                    return {"ok": True}
                return usr.create_user(c, body.get("username", ""), body.get("password", ""), nama=body.get("nama", ""), role=body.get("role", "viewer"))
            finally:
                c.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/users/delete")
    async def api_users_delete(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        uid = body.get("id") if isinstance(body, dict) else None
        if not uid:
            return JSONResponse({"ok": False, "error": "id kosong."})

        def _run():
            c = usr.connect()
            try:
                usr.init_db(c)
                return usr.delete_user(c, int(uid))
            finally:
                c.close()

        try:
            return JSONResponse(await run_in_threadpool(_run))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    # Seed admin awal (dipindah dari top-level web_app.py)
    try:
        _sc = usr.connect()
        _si = usr.seed_admin(_sc)
        _sc.close()
        if _si and _si.get("default_password"):
            print("[users] Admin awal: %s / %s  -- SEGERA GANTI via /users" % (_si["username"], _si["default_password"]))
    except Exception as _e:
        print("[users] seed dilewati:", _e)
