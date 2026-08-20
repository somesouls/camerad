# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/intentmap_db.py.
import sys as _sys
import knowledge.intentmap_db as _mod
_sys.modules[__name__] = _mod
