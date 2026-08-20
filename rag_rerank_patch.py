# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/rerank_patch.py.
# import rag_rerank_patch / from rag_rerank_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import rerank_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.rerank_patch as _mod
_sys.modules[__name__] = _mod
