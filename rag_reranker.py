# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/reranker.py.
# import rag_reranker / from rag_reranker import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import reranker  (dibersihkan di PR akhir).
import sys as _sys
import rag.reranker as _mod
_sys.modules[__name__] = _mod
