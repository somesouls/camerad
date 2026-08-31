# -*- coding: utf-8 -*-
"""awe/phone_dash_routes.py - halaman Dashboard & Pengguna Harian AWE Telepon.

Mendaftarkan dua rute HALAMAN (GET) langsung ke app FastAPI (meniru pola
awe/phone_autopull.register_app), karena awe/routes.py sudah di atas plafon push
dan tidak diedit. Data diambil di sisi klien dari endpoint yang sudah ada
POST /api/awe/phone/probe (aksi coverage / daily_users / daily_convs). Read-only,
terpisah total dari Chat.

  GET /awe/telepon/dashboard -> templates/awe_phone_dash.html  (active: awe_telepon_dash)
  GET /awe/telepon/pengguna  -> templates/awe_phone_users.html (active: awe_telepon_users)

Izin: middleware app_core memetakan /awe/* -> area 'awe' (butuh can_awe). Endpoint
data /api/awe/phone/probe tetap digerbang 'awe_manage' di phone_routes, sama
seperti menu Kelola Data Phone.
"""
from fastapi import Request

_ROUTES_DONE = False


async def awe_phone_dash_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_dash.html", "awe_telepon_dash")


async def awe_phone_users_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_users.html", "awe_telepon_users")


def register_app():
    """Daftarkan rute halaman dashboard & pengguna harian telepon (idempoten)."""
    global _ROUTES_DONE
    if _ROUTES_DONE:
        return
    from app_core import app
    app.add_api_route("/awe/telepon/dashboard", awe_phone_dash_page, methods=["GET"])
    app.add_api_route("/awe/telepon/pengguna", awe_phone_users_page, methods=["GET"])
    _ROUTES_DONE = True
