# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/recall_map.py.
# import eval_recall_map / from eval_recall_map import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import recall_map  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.recall_map as _mod
_sys.modules[__name__] = _mod
