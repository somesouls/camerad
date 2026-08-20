# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-10). Asli dipindah ke chat/frontend_routes.py.
import sys as _sys
import chat.frontend_routes as _mod
_sys.modules[__name__] = _mod
