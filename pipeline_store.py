# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/store.py.
import sys as _sys
import pipeline.store as _mod
_sys.modules[__name__] = _mod
