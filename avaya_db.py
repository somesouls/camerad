# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke avaya/db.py.
# import avaya_db / from avaya_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from avaya import db  (dibersihkan di PR akhir).
import sys as _sys
import avaya.db as _mod
_sys.modules[__name__] = _mod
