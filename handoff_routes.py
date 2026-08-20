# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routes.py.
import sys as _sys
import handoff.routes as _mod
_sys.modules[__name__] = _mod
