# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke sosmed/ingest.py.
# import sosmed_ingest / from sosmed_ingest import ... tetap jalan sampai pemanggil
# diperbarui ke: from sosmed import ingest  (dibersihkan di PR akhir).
import sys as _sys
import sosmed.ingest as _mod
_sys.modules[__name__] = _mod
