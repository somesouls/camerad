# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke peraturan/files.py.
# import peraturan_files / from peraturan_files import ... tetap jalan sampai pemanggil
# diperbarui ke: from peraturan import files  (dibersihkan di PR akhir).
import sys as _sys
import peraturan.files as _mod
_sys.modules[__name__] = _mod
