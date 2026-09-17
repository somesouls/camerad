# -*- coding: utf-8 -*-
"""routes/menu_access_routes.py — API Kelola Akses PER-TAUTAN MENU (per-link RBAC).

Daftarkan dengan:
    import routes.menu_access_routes as menu_access_routes
    menu_access_routes.register(app)

Semua endpoint di bawah prefix /api/menu-access -> area 'users' + aksi 'admin'
(lihat _route_area & _route_action di app_core), jadi hanya peran ber-cap
'admin' yang boleh mengaksesnya. Modul ini ADITIF: hanya menyentuh tabel milik
db/menu_catalog.py (role_menus, user_menu_grants); users_db & base.html tak
diubah.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import db.menu_catalog as mc


def register(app):
    @app.get("/api/menu-access/catalog")
    async def api_menu_catalog(request: Request):
        # Katalog grup + tautan menu (statis) untuk membangun UI accordion.
        try:
            data = await run_in_threadpool(mc.catalog)
            return JSONResponse({
                "ok": True,
                "groups": data.get("groups", []),
                "menus": data.get("menus", []),
                "baseline": sorted(mc._BASELINE_MENUS),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/menu-access/role/{role_key}")
    async def api_menu_role_get(request: Request, role_key: str):
        if not role_key:
            return JSONResponse({"ok": False, "error": "role_key kosong."})

        def _run():
            return mc.get_role_menus(role_key)

        try:
            info = await run_in_threadpool(_run)
            return JSONResponse({
                "ok": True,
                "role_key": role_key,
                "configured": bool(info.get("configured")),
                "menus": info.get("menus", []),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/menu-access/role/save")
    async def api_menu_role_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        role_key = (body.get("role_key") or "").strip()
        if not role_key:
            return JSONResponse({"ok": False, "error": "role_key kosong."})
        menus = body.get("menus") or []

        def _run():
            return mc.set_role_menus(role_key, menus)

        try:
            info = await run_in_threadpool(_run)
            return JSONResponse({
                "ok": True,
                "role_key": role_key,
                "configured": bool(info.get("configured")),
                "menus": info.get("menus", []),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/menu-access/role/reset")
    async def api_menu_role_reset(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        role_key = (body.get("role_key") or "").strip() if isinstance(body, dict) else ""
        if not role_key:
            return JSONResponse({"ok": False, "error": "role_key kosong."})

        def _run():
            mc.clear_role_menus(role_key)
            return mc.get_role_menus(role_key)

        try:
            info = await run_in_threadpool(_run)
            return JSONResponse({
                "ok": True,
                "role_key": role_key,
                "configured": bool(info.get("configured")),
                "menus": info.get("menus", []),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/menu-access/user/{user_id}")
    async def api_menu_user_get(request: Request, user_id: int):
        def _run():
            return mc.get_user_menus(int(user_id))

        try:
            grants = await run_in_threadpool(_run)
            return JSONResponse({"ok": True, "user_id": int(user_id), "grants": grants})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/menu-access/user/save")
    async def api_menu_user_save(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        uid = body.get("user_id") or body.get("id")
        if not uid:
            return JSONResponse({"ok": False, "error": "user_id kosong."})
        grants = body.get("grants") or {}

        def _run():
            return mc.set_user_menus(int(uid), grants)

        try:
            saved = await run_in_threadpool(_run)
            return JSONResponse({"ok": True, "user_id": int(uid), "grants": saved})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})
