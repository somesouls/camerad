# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/router.py.
# import rag_router / from rag_router import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import router  (dibersihkan di PR akhir).
import sys as _sys
import rag.router as _mod
_sys.modules[__name__] = _mod
