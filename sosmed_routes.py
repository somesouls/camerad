# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sosmed/routes.py.
# import sosmed_routes / from sosmed_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from sosmed import routes  (dibersihkan di PR akhir).
import sys as _sys
import sosmed.routes as _mod
_sys.modules[__name__] = _mod
