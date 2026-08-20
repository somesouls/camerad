# -*- coding: utf-8 -*-
"""Namespace paket untuk pipeline_helpers.

Tahap scaffold reorg: expose modul root pipeline_helpers lewat paket pipeline.
"""
import sys as _sys
import pipeline_helpers as _mod
_sys.modules[__name__] = _mod
