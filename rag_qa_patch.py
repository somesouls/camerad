# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/qa_patch.py.
# import rag_qa_patch / from rag_qa_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import qa_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.qa_patch as _mod
_sys.modules[__name__] = _mod
