# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sop/db.py.
# import sop_db / from sop_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from sop import db  (dibersihkan di PR akhir).
import sys as _sys
import sop.db as _mod
_sys.modules[__name__] = _mod
