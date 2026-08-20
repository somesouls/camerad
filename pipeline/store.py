# -*- coding: utf-8 -*-
"""Namespace paket untuk pipeline_store.

Tahap scaffold reorg: expose modul root pipeline_store lewat paket pipeline.
Catatan penting untuk tahap pemindahan byte-exact berikutnya: pipeline_store.py
memiliki _BASE_DIR berbasis __file__, sehingga saat isi dipindah ke
pipeline/store.py harus dipatch naik 1 level agar pipeline_store.db tetap di root.
"""
import sys as _sys
import pipeline_store as _mod
_sys.modules[__name__] = _mod
