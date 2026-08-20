# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/engine.py.
# import rag_engine / from rag_engine import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import engine  (dibersihkan di PR akhir).
import sys as _sys
import rag.engine as _mod
_sys.modules[__name__] = _mod
