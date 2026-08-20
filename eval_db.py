# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/db.py.
# import eval_db / from eval_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import db  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.db as _mod
_sys.modules[__name__] = _mod
