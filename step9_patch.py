# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-17). Asli dipindah ke pipeline/step9_patch.py
import sys as _sys
import pipeline.step9_patch as _mod
_sys.modules[__name__] = _mod
