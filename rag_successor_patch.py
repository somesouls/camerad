# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/successor_patch.py.
# import rag_successor_patch / from rag_successor_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import successor_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.successor_patch as _mod
_sys.modules[__name__] = _mod
