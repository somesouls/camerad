# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/routes.py.
# import eval_routes / from eval_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import routes  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.routes as _mod
_sys.modules[__name__] = _mod
