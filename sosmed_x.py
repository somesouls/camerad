# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sosmed/x.py.
# import sosmed_x / from sosmed_x import ... tetap jalan sampai pemanggil
# diperbarui ke: from sosmed import x  (dibersihkan di PR akhir).
import sys as _sys
import sosmed.x as _mod
_sys.modules[__name__] = _mod
