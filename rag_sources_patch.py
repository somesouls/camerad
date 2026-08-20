# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/sources_patch.py.
# import rag_sources_patch / from rag_sources_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import sources_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.sources_patch as _mod
_sys.modules[__name__] = _mod
