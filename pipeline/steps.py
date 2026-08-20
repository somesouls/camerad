# -*- coding: utf-8 -*-
"""Namespace paket untuk pipeline_steps.

Tahap scaffold reorg: expose modul root pipeline_steps lewat paket pipeline.
"""
import sys as _sys
import pipeline_steps as _mod
_sys.modules[__name__] = _mod
