# -*- coding: utf-8 -*-
"""Public landing page routes.

The landing experience is deliberately isolated from the authenticated
application shell so it can evolve into Camerad's next design system without
changing existing product screens.
"""
from fastapi.responses import HTMLResponse

from app_core import _load_html


def register(app):
    @app.get("/", response_class=HTMLResponse)
    async def public_landing():
        return HTMLResponse(_load_html("landing/landing.html"))
