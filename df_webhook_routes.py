# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-9). Asli dipindah ke df_webhook/routes.py.
import sys as _sys
import df_webhook.routes as _mod
_sys.modules[__name__] = _mod
