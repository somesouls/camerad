# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke awe/assess.py.
# import awe_assess / from awe_assess import ... tetap jalan sampai pemanggil
# diperbarui ke: from awe import assess  (dibersihkan di PR akhir).
import sys as _sys
import awe.assess as _mod
_sys.modules[__name__] = _mod
