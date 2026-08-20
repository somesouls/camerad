# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/harness.py.
# import eval_harness / from eval_harness import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import harness  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.harness as _mod
_sys.modules[__name__] = _mod
