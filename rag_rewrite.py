# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/rewrite.py.
# import rag_rewrite / from rag_rewrite import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import rewrite  (dibersihkan di PR akhir).
import sys as _sys
import rag.rewrite as _mod
_sys.modules[__name__] = _mod
