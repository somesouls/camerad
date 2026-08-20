# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke awe/overview.py.
# import awe_overview / from awe_overview import ... tetap jalan sampai pemanggil
# diperbarui ke: from awe import overview  (dibersihkan di PR akhir).
import sys as _sys
import awe.overview as _mod
_sys.modules[__name__] = _mod
