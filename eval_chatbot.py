# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke evaluation/chatbot.py.
# import eval_chatbot / from eval_chatbot import ... tetap jalan sampai pemanggil
# diperbarui ke: from evaluation import chatbot  (dibersihkan di PR akhir).
import sys as _sys
import evaluation.chatbot as _mod
_sys.modules[__name__] = _mod
