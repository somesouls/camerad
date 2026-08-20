# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/kamus_db.py.
# import rag_kamus_db / from rag_kamus_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import kamus_db  (dibersihkan di PR akhir).
import sys as _sys
import rag.kamus_db as _mod
_sys.modules[__name__] = _mod
