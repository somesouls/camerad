# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/semantic.py.
import sys as _sys
import knowledge.semantic as _mod
_sys.modules[__name__] = _mod
