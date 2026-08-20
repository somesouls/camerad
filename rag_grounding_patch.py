# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/grounding_patch.py.
# import rag_grounding_patch / from rag_grounding_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import grounding_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.grounding_patch as _mod
_sys.modules[__name__] = _mod
