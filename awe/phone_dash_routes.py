# -*- coding: utf-8 -*-
"""awe/phone_dash_routes.py - halaman Dashboard & analitik AWE Telepon.

Mendaftarkan rute HALAMAN (GET) langsung ke app FastAPI (meniru pola
awe/phone_autopull.register_app), karena awe/routes.py sudah di atas plafon push
dan tidak diedit. Data diambil di sisi klien dari endpoint yang sudah ada
POST /api/awe/phone/probe (aksi coverage / list / detail / daily_users /
daily_convs). Read-only, terpisah total dari Chat.

  GET /awe/telepon/dashboard  -> templates/awe_phone_dash.html   (active: awe_telepon_dash)
  GET /awe/telepon/pengguna   -> templates/awe_phone_users.html  (active: awe_telepon_users)
  GET /awe/telepon/coverage   -> templates/awe_phone_cov.html    (active: awe_telepon_cov)
  GET /awe/telepon/taksonomi  -> templates/awe_phone_tax.html    (active: awe_telepon_tax)
  GET /awe/telepon/sentimen   -> templates/awe_phone_sen.html    (active: awe_telepon_sen)
  GET /awe/telepon/percakapan -> templates/awe_phone_detail.html (active: awe_telepon_detail)

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


async def awe_phone_cov_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_cov.html", "awe_telepon_cov")


async def awe_phone_tax_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_tax.html", "awe_telepon_tax")


async def awe_phone_sen_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_sen.html", "awe_telepon_sen")


async def awe_phone_detail_page(request: Request):
    from app_core import render_page
    return render_page(request, "awe_phone_detail.html", "awe_telepon_detail")


def register_app():
    """Daftarkan semua rute halaman analitik telepon (idempoten)."""
    global _ROUTES_DONE
    if _ROUTES_DONE:
        return
    from app_core import app
    app.add_api_route("/awe/telepon/dashboard", awe_phone_dash_page, methods=["GET"])
    app.add_api_route("/awe/telepon/pengguna", awe_phone_users_page, methods=["GET"])
    app.add_api_route("/awe/telepon/coverage", awe_phone_cov_page, methods=["GET"])
    app.add_api_route("/awe/telepon/taksonomi", awe_phone_tax_page, methods=["GET"])
    app.add_api_route("/awe/telepon/sentimen", awe_phone_sen_page, methods=["GET"])
    app.add_api_route("/awe/telepon/percakapan", awe_phone_detail_page, methods=["GET"])
    _ROUTES_DONE = True
