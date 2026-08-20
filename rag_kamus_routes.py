# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/kamus_routes.py.
# import rag_kamus_routes / from rag_kamus_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import kamus_routes  (dibersihkan di PR akhir).
import sys as _sys
import rag.kamus_routes as _mod
_sys.modules[__name__] = _mod
