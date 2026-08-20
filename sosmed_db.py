# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sosmed/db.py.
# import sosmed_db / from sosmed_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from sosmed import db  (dibersihkan di PR akhir).
import sys as _sys
import sosmed.db as _mod
_sys.modules[__name__] = _mod
