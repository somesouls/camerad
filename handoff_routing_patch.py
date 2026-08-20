# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routing_patch.py.
import sys as _sys
import handoff.routing_patch as _mod
_sys.modules[__name__] = _mod
