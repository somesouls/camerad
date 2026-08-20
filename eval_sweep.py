# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/sweep.py.
# import eval_sweep / from eval_sweep import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import sweep  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.sweep as _mod
_sys.modules[__name__] = _mod
