# -*- coding: utf-8 -*-
"""Paket finetune: pembuatan dataset & panduan LoRA (QLoRA) untuk camerad.

Tiga builder dataset SFT dari knowledge base yang sudah ada:
  * build_intent    -> klasifikasi intent (Dataset #1)
  * build_faq       -> FAQ multi-turn sosmed/livechat (Dataset #2)
  * build_grounded  -> RAG-grounded peraturan & SOP (Dataset #3)

Semua builder menghasilkan format CHAT terpadu (OpenAI/ShareGPT) yang langsung
dipakai Unsloth/Axolotl untuk QLoRA dan identik dengan skema serving vLLM.
Lihat finetune/README.md untuk strategi, base model, training & serving.
"""
