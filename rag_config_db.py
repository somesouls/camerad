# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke rag/config_db.py.
# import rag_config_db / from rag_config_db import ... tetap jalan sampai pemanggil
# diperbarui ke: from rag import config_db  (dibersihkan di PR akhir).
import sys as _sys
import rag.config_db as _mod
_sys.modules[__name__] = _mod
