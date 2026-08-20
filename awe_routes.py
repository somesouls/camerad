# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke awe/routes.py.
# import awe_routes / from awe_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from awe import routes  (dibersihkan di PR akhir).
import sys as _sys
import awe.routes as _mod
_sys.modules[__name__] = _mod
