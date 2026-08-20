# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/golden_db.py.
# import rag_golden_db / from rag_golden_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import golden_db  (dibersihkan di PR akhir).
import sys as _sys
import rag.golden_db as _mod
_sys.modules[__name__] = _mod
