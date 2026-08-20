# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/stats.py.
import sys as _sys
import knowledge.stats as _mod
_sys.modules[__name__] = _mod
