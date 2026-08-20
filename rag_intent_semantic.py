# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/intent_semantic.py.
# import rag_intent_semantic / from rag_intent_semantic import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import intent_semantic  (dibersihkan di PR akhir).
import sys as _sys
import rag.intent_semantic as _mod
_sys.modules[__name__] = _mod
