# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/judge.py.
# import eval_judge / from eval_judge import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import judge  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.judge as _mod
_sys.modules[__name__] = _mod
