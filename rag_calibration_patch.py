# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/calibration_patch.py.
# import rag_calibration_patch / from rag_calibration_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import calibration_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.calibration_patch as _mod
_sys.modules[__name__] = _mod
