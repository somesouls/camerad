# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/routes.py.
# import rag_routes / from rag_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import routes  (dibersihkan di PR akhir).
import sys as _sys
import rag.routes as _mod
_sys.modules[__name__] = _mod
