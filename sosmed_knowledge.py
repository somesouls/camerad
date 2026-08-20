# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sosmed/knowledge.py.
# import sosmed_knowledge / from sosmed_knowledge import ... tetap jalan sampai pemanggil
# diperbarui ke: from sosmed import knowledge  (dibersihkan di PR akhir).
import sys as _sys
import sosmed.knowledge as _mod
_sys.modules[__name__] = _mod
