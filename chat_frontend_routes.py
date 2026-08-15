import os
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from google.cloud import dialogflow

# Import konfigurasi dan fungsi render_page bawaan dari app_core buatan Opus
from app_core import CONFIG, render_page

# Konfigurasi webhook (untuk membaca kalimat fallback resmi). Fail-open bila
# modul tak tersedia.
try:
    import df_webhook_db as dfdb
except Exception:
    dfdb = None


def register(app):
    
    # --- 1. RUTE UNTUK MENAMPILKAN HALAMAN WEB (chat.html) ---
    @app.get("/livechat", response_class=HTMLResponse)
    async def tampilkan_chat(request: Request):
        # Memanfaatkan fungsi bawaan Opus agar rapi
        return render_page(request, "chat.html")


    # --- 2. RUTE API UNTUK MENGIRIM PESAN KE DIALOGFLOW ---
    @app.post("/api/chat/detect")
    async def detect_intent_chat(request: Request):
        data = await request.json()
        text = data.get("text", "")
        session_id = data.get("session_id", str(uuid.uuid4()))

        project_id = CONFIG.get("camerad_project_id")

        # detect_intent() bersifat SINKRON/blocking (gRPC). Jalankan di
        # threadpool agar event loop tetap bebas melayani callback webhook
        # /api/df/webhook (dipanggil Dialogflow ke server yang SAMA). Tanpa ini
        # terjadi self-deadlock -> Dialogflow time out ~5 dtk -> respons statis.
        def _detect():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CONFIG["camerad_service_account_file"]
            session_client = dialogflow.SessionsClient()
            session = session_client.session_path(project_id, session_id)
            text_input = dialogflow.TextInput(text=text, language_code="id")
            query_input = dialogflow.QueryInput(text=text_input)
            return session_client.detect_intent(
                request={"session": session, "query_input": query_input}
            )

        try:
            response = await run_in_threadpool(_detect)
            qr = response.query_result
            intent = qr.intent
            reply = qr.fulfillment_text or ""

            # -------------------------------------------------------------
            # DETEKSI FALLBACK YANG BENAR
            # Balasan dianggap "cadangan" HANYA bila webhook RAG tidak
            # mengirim jawaban, sehingga Dialogflow membalas teks statis =
            # kalimat fallback yang dikonfigurasi (atau balasan kosong).
            #
            # PENTING: intent.is_fallback (Default Fallback Intent) BUKAN
            # penanda kegagalan. Justru lewat Default Fallback Intent-lah
            # semua pertanyaan dirutekan ke webhook RAG. Memakai
            # intent.is_fallback membuat SEMUA jawaban RAG salah ditandai
            # sebagai "cadangan".
            # -------------------------------------------------------------
            fb = ""
            try:
                if dfdb is not None:
                    fb = (dfdb.get_config().get("fallback") or "").strip()
            except Exception:
                fb = ""

            r = reply.strip()
            is_fallback = (not r) or (bool(fb) and r == fb)

            # webhook_source terisi bila webhook kami membalas (kami set
            # "source": "camerad-kringpajak"). Dipakai sebagai sinyal positif
            # tambahan (bila tersedia di versi library).
            webhook_source = getattr(qr, "webhook_source", "") or ""
            if webhook_source:
                is_fallback = False

            return {
                "reply": reply,
                "session_id": session_id,
                "intent": getattr(intent, "display_name", "") or "",
                "confidence": round(
                    float(getattr(qr, "intent_detection_confidence", 0.0) or 0.0), 3
                ),
                "webhook_source": webhook_source,
                "is_fallback": is_fallback,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
