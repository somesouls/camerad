# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke awe/analytics.py.
# import awe_analytics / from awe_analytics import ... tetap jalan sampai pemanggil
# diperbarui ke: from awe import analytics  (dibersihkan di PR akhir).
import sys as _sys
import awe.analytics as _mod
_sys.modules[__name__] = _mod
