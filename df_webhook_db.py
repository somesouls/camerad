# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-9). Asli dipindah ke df_webhook/db.py.
import sys as _sys
import df_webhook.db as _mod
_sys.modules[__name__] = _mod
