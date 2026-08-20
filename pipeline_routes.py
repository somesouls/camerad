# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/routes.py.
# import pipeline_routes / from pipeline_routes import ... tetap jalan sampai
# pemanggil diperbarui ke: from pipeline import routes.
import sys as _sys
import pipeline.routes as _mod
_sys.modules[__name__] = _mod
