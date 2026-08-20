# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/routes.py.
# import sop_routes / from sop_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import routes  (dibersihkan di PR akhir).
import sys as _sys
import sop.routes as _mod
_sys.modules[__name__] = _mod
