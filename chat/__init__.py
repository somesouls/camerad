# -*- coding: utf-8 -*-
"""Paket chat Camerad Studio.

Berisi route kanal tanya-AI:
  - frontend_routes : widget Live Chat (/livechat) + jembatan Dialogflow ES
                      (Opsi B echo/poll) untuk chat.html.
  - agent_routes    : chat RAG \"Agent Kring Pajak\" (halaman utama '/') +
                      halaman konfigurasi mesin RAG (/rag-agent, /rag-chatbot).

Root module lama (chat_frontend_routes, agent_chat_routes) tetap tersedia
sebagai shim kompatibilitas.
"""
