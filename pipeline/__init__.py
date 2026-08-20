# -*- coding: utf-8 -*-
"""Paket pipeline Dialogflow/Avaya.

Reorg bertahap dari modul root:
- pipeline_routes.py
- pipeline_helpers.py
- pipeline_steps.py
- pipeline_store.py

Tahap ini memasang namespace paket dulu agar import baru bisa mulai memakai:
    from pipeline import routes, helpers, steps, store

Modul root masih menjadi sumber kebenaran sementara dan tetap dipertahankan
sebagai kompatibilitas mundur sampai pemindahan byte-exact tahap berikutnya.
"""
