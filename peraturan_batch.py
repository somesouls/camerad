# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/batch.py.
# import peraturan_batch / from peraturan_batch import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import batch  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.batch as _mod
_sys.modules[__name__] = _mod
