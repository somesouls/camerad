# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/db.py.
# import peraturan_db / from peraturan_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import db  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.db as _mod
_sys.modules[__name__] = _mod
