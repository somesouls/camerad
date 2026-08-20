# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/disambig_db.py.
import sys as _sys
import knowledge.disambig_db as _mod
_sys.modules[__name__] = _mod
