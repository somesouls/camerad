# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/batch.py.
# import sop_batch / from sop_batch import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import batch  (dibersihkan di PR akhir).
import sys as _sys
import sop.batch as _mod
_sys.modules[__name__] = _mod
