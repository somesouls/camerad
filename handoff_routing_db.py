# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routing_db.py.
import sys as _sys
import handoff.routing_db as _mod
_sys.modules[__name__] = _mod
