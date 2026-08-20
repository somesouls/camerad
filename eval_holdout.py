# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/holdout.py.
# import eval_holdout / from eval_holdout import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import holdout  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.holdout as _mod
_sys.modules[__name__] = _mod
