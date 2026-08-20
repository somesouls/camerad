# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke avaya/pipeline.py.
# import avaya_pipeline / from avaya_pipeline import ... tetap jalan sampai pemanggil
# diperbarui ke: from avaya import pipeline  (dibersihkan di PR akhir).
import sys as _sys
import avaya.pipeline as _mod
_sys.modules[__name__] = _mod
