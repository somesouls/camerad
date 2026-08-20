# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/drilldown_patch.py.
# import rag_drilldown_patch / from rag_drilldown_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import drilldown_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.drilldown_patch as _mod
_sys.modules[__name__] = _mod
