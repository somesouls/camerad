# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/sampler.py.
# import eval_sampler / from eval_sampler import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import sampler  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.sampler as _mod
_sys.modules[__name__] = _mod
