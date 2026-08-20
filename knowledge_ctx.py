# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/ctx.py.
import sys as _sys
import knowledge.ctx as _mod
_sys.modules[__name__] = _mod
