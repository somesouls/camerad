# -*- coding: utf-8 -*-
"""Bootstrap AWE Avaya di proses web_app.py.

Tujuan:
- /api/avaya-* tidak lagi wajib hidup di llm_fix_final_combined.py:8000.
- web_app.py:8080 dapat menjalankan analisis AWE langsung dalam satu proses.
- Shim root avaya_pipeline + patch root tetap dipakai agar kompatibel dengan
  reorg avaya/ yang sudah landed.

Modul ini sengaja kecil dan fail-soft. Diimpor dari app_core agar route Avaya
terpasang sebelum web_app.py mulai melayani request, tanpa memindahkan ulang
kode pipeline Avaya yang besar.
"""

import os

_BOOTSTRAPPED = False


def register(app):
    """Pasang route Avaya ke FastAPI app web utama."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return app

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    api_key = os.environ.get("PIPELINE_API_KEY", "sam-n8n-secret")

    try:
        import llm_client
        import avaya_pipeline

        def _avaya_generate(prompts, max_new_tokens=256):
            return llm_client.generate(prompts, max_new_tokens=max_new_tokens)

        def _load_llm():
            try:
                llm_client.init_client()
            except Exception as exc:
                print("[AVAYA-WEB] peringatan init LLM:", exc, flush=True)

        def _unload_llm():
            return None

        qwen_ctx = {
            "generate": _avaya_generate,
            "load": _load_llm,
            "unload": _unload_llm,
        }

        avaya_pipeline.register_avaya_routes(app, qwen_ctx=qwen_ctx, api_key=api_key)
        print("[AVAYA-WEB] route Avaya aktif di web_app.py (satu proses).", flush=True)

        # Patch tetap root sesuai desain reorg: monkeypatch terhadap shim
        # avaya_pipeline yang resolve ke avaya.pipeline.
        try:
            import avaya_speedpatch
            avaya_speedpatch.apply()
            print("[AVAYA-WEB] speedpatch aktif.", flush=True)
        except Exception as exc:
            print("[AVAYA-WEB] speedpatch dilewati:", exc, flush=True)

        try:
            import avaya_dashpatch
            avaya_dashpatch.apply()
            print("[AVAYA-WEB] dashpatch aktif.", flush=True)
        except Exception as exc:
            print("[AVAYA-WEB] dashpatch dilewati:", exc, flush=True)

        _BOOTSTRAPPED = True
    except Exception as exc:
        print("[AVAYA-WEB] route Avaya tidak dimuat:", exc, flush=True)

    return app
