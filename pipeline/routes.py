# -*- coding: utf-8 -*-
"""Namespace paket untuk pipeline_routes.

Tahap scaffold reorg: expose modul root pipeline_routes lewat paket pipeline.
Pemindahan byte-exact ke file ini dilakukan pada tahap berikutnya setelah
boot-test scaffold hijau.
"""
import sys as _sys
import pipeline_routes as _mod
_sys.modules[__name__] = _mod
