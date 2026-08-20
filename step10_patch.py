# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-17). Asli dipindah ke pipeline/step10_patch.py
import sys as _sys
import pipeline.step10_patch as _mod
_sys.modules[__name__] = _mod
