# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/semantic.py.
# import peraturan_semantic / from peraturan_semantic import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import semantic  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.semantic as _mod
_sys.modules[__name__] = _mod
