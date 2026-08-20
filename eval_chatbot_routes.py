# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/chatbot_routes.py.
# import eval_chatbot_routes / from eval_chatbot_routes import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import chatbot_routes  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.chatbot_routes as _mod
_sys.modules[__name__] = _mod
