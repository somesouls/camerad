import os
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from google.cloud import dialogflow

# Import konfigurasi dan fungsi render_page bawaan dari app_core buatan Opus
from app_core import CONFIG, render_page

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

        # =====================================================================
        # PENTING (perbaikan self-deadlock 5 detik):
        # detect_intent() milik Dialogflow bersifat SINKRON/blocking (gRPC).
        # Bila dipanggil langsung di dalam handler async, ia MEMBLOKIR event
        # loop. Padahal saat detect_intent menunggu, Dialogflow memanggil
        # balik webhook /api/df/webhook ke server yang SAMA. Karena event loop
        # terblokir, callback webhook itu tidak bisa dilayani tepat waktu ->
        # Dialogflow time out ~5 dtk -> jatuh ke respons STATIS intent
        # (mis. "Salam!", "Hai!") alih-alih jawaban mesin RAG.
        #
        # Solusi: jalankan detect_intent di threadpool sehingga event loop
        # tetap bebas melayani callback webhook secara paralel.
        # =====================================================================
        def _detect():
            # Kredensial service account khusus camerad
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CONFIG["camerad_service_account_file"]
            session_client = dialogflow.SessionsClient()
            session = session_client.session_path(project_id, session_id)
            text_input = dialogflow.TextInput(text=text, language_code="id")
            query_input = dialogflow.QueryInput(text=text_input)
            return session_client.detect_intent(
                request={"session": session, "query_input": query_input}
            )

        try:
            # Kirim teks ke Dialogflow (di threadpool, tidak memblokir loop)
            response = await run_in_threadpool(_detect)
            qr = response.query_result
            intent = qr.intent

            # is_fallback = True untuk Default Fallback Intent. Juga dianggap
            # fallback bila teks balasan kosong (mis. webhook time out).
            is_fallback = bool(getattr(intent, "is_fallback", False))
            reply = qr.fulfillment_text or ""
            if not reply.strip():
                is_fallback = True

            return {
                "reply": reply,
                "session_id": session_id,
                "intent": getattr(intent, "display_name", "") or "",
                "confidence": round(
                    float(getattr(qr, "intent_detection_confidence", 0.0) or 0.0), 3
                ),
                "is_fallback": is_fallback,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
