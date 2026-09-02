# -*- coding: utf-8 -*-
"""Voicebot engine (lokal / self-host) untuk Camerad Studio.

Mesin suara API-first yang berjalan PENUH LOKAL (ada data WP sensitif -> tidak
boleh keluar ke cloud):

    audio -> STT (faster-whisper) -> NLU hybrid (embedding lokal + LLM fallback)
          -> dialog/handoff -> TTS (Piper) -> log (feed pipeline analisis)

Komponen di-REUSE dari proyek existing bila ada:
  - STT    : avaya.phone_stt.transcribe_file (faster-whisper, id-ID)
  - LLM    : common.llm_client (set LLM_PROVIDER=local utk vLLM/Ollama on-prem)
  - Handoff: handoff.routing_db (perutean layanan)

Semua impor berat dilakukan LAZY + fail-soft agar app tetap boot walau model
belum terpasang. Daftarkan route dengan:

    import voicebot.routes as voicebot_routes
    voicebot_routes.register(app)
"""
