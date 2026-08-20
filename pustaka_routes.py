# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-8). Asli dipindah ke pustaka/routes.py.
import sys as _sys
import pustaka.routes as _mod
_sys.modules[__name__] = _mod
