import os
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
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
        
        # Menggunakan kredensial dari file JSON yang disetel di CONFIG
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CONFIG["camerad_service_account_file"]
        
        session_client = dialogflow.SessionsClient()
        session = session_client.session_path(project_id, session_id)
        
        text_input = dialogflow.TextInput(text=text, language_code="id")
        query_input = dialogflow.QueryInput(text=text_input)
        
        try:
            # Kirim teks ke Dialogflow
            response = session_client.detect_intent(
                request={"session": session, "query_input": query_input}
            )
            return {"reply": response.query_result.fulfillment_text, "session_id": session_id}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})