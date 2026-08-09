# -*- coding: utf-8 -*-
"""audit_tp_routes.py — Menu "Audit Training Phrase".

Fitur berdiri sendiri (BUKAN bagian rail Step 1-16 Analisis Dialogflow).
Tujuan: higiene data intent. Menyandingkan training phrase ANTAR intent memakai
SBERT (model sama seperti pipeline) untuk menemukan:
  1) Konflik antar-intent  : frasa mirip yang berada di intent BERBEDA
                             (penyebab utama match rate turun / tumpang tindih).
  2) Duplikat dalam intent : frasa nyaris kembar di intent SAMA (seed mubazir).

Input = ZIP "Database Intent Dialogflow" (output Step 3/13) ATAU langsung file
xlsx Training Phrase berkolom ["ID