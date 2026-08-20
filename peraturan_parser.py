# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/parser.py.
# import peraturan_parser / from peraturan_parser import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import parser  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.parser as _mod
_sys.modules[__name__] = _mod
