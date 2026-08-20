# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/routes.py.
# import peraturan_routes / from peraturan_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import routes  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.routes as _mod
_sys.modules[__name__] = _mod
