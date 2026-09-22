# -*- coding: utf-8 -*-
"""Public landing and authenticated application entry."""
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app_core import _load_html, _user_from_token, render_page
import db.users_db as usr

def _current_user(request):
    token=request.cookies.get("session")
    return _user_from_token(token) if token else None

def register(app):
    @app.get("/",response_class=HTMLResponse)
    async def public_landing(request:Request):
        if _current_user(request): return RedirectResponse("/app",status_code=302)
        return HTMLResponse(_load_html("landing/landing.html"))
    @app.get("/app")
    async def authenticated_home(request:Request):
        user=_current_user(request)
        if not user: return RedirectResponse(f"/login?next={quote('/app',safe='')}",status_code=302)
        if not usr.area_allowed(user.get("role"),"chat",user_id=user.get("id")): return RedirectResponse("/profil",status_code=302)
        return render_page(request,"index.html","")
