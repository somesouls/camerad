# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-16). Asli dipindah ke awe/botfilter_patch.py
import sys as _sys
import awe.botfilter_patch as _mod
_sys.modules[__name__] = _mod
