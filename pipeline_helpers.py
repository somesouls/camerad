# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/helpers.py.
import sys as _sys
import pipeline.helpers as _mod
_sys.modules[__name__] = _mod
