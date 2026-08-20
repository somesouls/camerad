# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke awe/deflection.py.
# import awe_deflection / from awe_deflection import ... tetap jalan sampai pemanggil
# diperbarui ke: from awe import deflection  (dibersihkan di PR akhir).
import sys as _sys
import awe.deflection as _mod
_sys.modules[__name__] = _mod
