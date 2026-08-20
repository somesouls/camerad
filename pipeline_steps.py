# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/steps.py.
import sys as _sys
import pipeline.steps as _mod
_sys.modules[__name__] = _mod
