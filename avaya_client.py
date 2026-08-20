# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke avaya/client.py.
# import avaya_client / from avaya_client import ... tetap jalan sampai pemanggil
# diperbarui ke: from avaya import client  (dibersihkan di PR akhir).
import sys as _sys
import avaya.client as _mod
_sys.modules[__name__] = _mod
