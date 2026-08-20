# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-15). Asli dipindah ke rag/domain_patch.py.
# import rag_domain_patch / from rag_domain_patch import ... tetap jalan sampai pemanggil diperbarui
# ke: from rag import domain_patch  (dibersihkan di PR akhir).
import sys as _sys
import rag.domain_patch as _mod
_sys.modules[__name__] = _mod
