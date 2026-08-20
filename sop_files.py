# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/files.py.
# import sop_files / from sop_files import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import files  (dibersihkan di PR akhir).
import sys as _sys
import sop.files as _mod
_sys.modules[__name__] = _mod
