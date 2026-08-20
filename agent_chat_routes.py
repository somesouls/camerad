# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-10). Asli dipindah ke chat/agent_routes.py.
import sys as _sys
import chat.agent_routes as _mod
_sys.modules[__name__] = _mod
