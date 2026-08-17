import os
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from google.cloud import dialogflow

# Import konfigurasi dan fungsi render_page bawaan dari app_core.
from app_core import CONFIG, render_page

# Kalimat fallback resmi (fail-open bila modul tak tersedia).
try:
    import df_webhook_db as dfdb
except Exception:
    dfdb = None

# Job-store Opsi B berada di df_webhook_routes (proses SAMA). Dipakai untuk
# membaca status/hasil komputasi RAG + durasi backend. Fail-open.
try:
    import df_webhook_routes as dfw
except Exception:
    dfw = None

# Teks penanda giliran echo/poll (harus sama dengan webhook & chat.html).
SENTINEL_POLL = getattr(dfw, "SENTINEL_POLL", "__CAMERAD_POLL__")

# ---- Konfigurasi HANDOFF ke agen langsung (live chat) -----------------------
# Nama parameter entitas antrean agen pada intent handoff Dialogflow camerad,
# mis. intent "System_System_Hubungi Agent Connector" dengan Required Parameter
# name @Agent_Kring_Pajak:$agent_kring_pajak. Bisa di-override lewat env.
HANDOFF_PARAM = os.environ.get("CAMERAD_HANDOFF_PARAM", "agent_kring_pajak")
# Pola nama intent yang berarti "minta dihubungkan ke agen" (case-insensitive).
HANDOFF_INTENT_HINTS = (
    "hubungi agent", "hubungi agen", "live agent", "agent connector",
    "agent-foll", "hubungi agent connector",
)


def _to_dict(msg):
    """Ubah pesan/Struct protobuf Dialogflow menjadi dict biasa (fail-open)."""
    if msg is None:
        return {}
    try:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(msg._pb if hasattr(msg, "_pb") else msg)
    except Exception:
        pass
    try:
        return dict(msg)
    except Exception:
        return {}


def _extract_payloads(qr):
    """Kumpulkan semua custom payload (enrichment) dari fulfillmentMessages."""
    out = []
    try:
        for m in (qr.fulfillment_messages or []):
            md = _to_dict(m)
            if isinstance(md, dict) and md.get("payload"):
                out.append(md["payload"])
    except Exception:
        pass
    return out


def _agent_queue(params, payloads):
    """Nilai antrean agen: dari parameter entitas, atau dari custom payload."""
    v = params.get(HANDOFF_PARAM) if isinstance(params, dict) else None
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v if str(x).strip())
    if v:
        return str(v).strip()
    for p in payloads:
        if isinstance(p, dict):
            for k in ("queue", "agent_queue", "agentQueue", "skill",
                      "target", "antrean", HANDOFF_PARAM):
                if p.get(k):
                    val = p[k]
                    if isinstance(val, (list, tuple)):
                        val = ", ".join(str(x) for x in val if str(x).strip())
                    return str(val).strip()
    return ""


def _is_handoff(intent_name, params, payloads):
    """True bila giliran ini memicu handoff ke agen langsung."""
    nm = (intent_name or "").lower()
    if any(h in nm for h in HANDOFF_INTENT_HINTS):
        return True
    if isinstance(params, dict) and params.get(HANDOFF_PARAM):
        return True
    for p in payloads:
        if not isinstance(p, dict):
            continue
        for k in ("handoff", "live_agent", "liveAgent", "transferToAgent",
                  "transfer_to_agent", "agent_handoff", "toLiveAgent"):
            if p.get(k):
                return True
    return False


def register(app):

    # --- 1. Halaman web widget chat ---
    @app.get("/livechat", response_class=HTMLResponse)
    async def tampilkan_chat(request: Request):
        return render_page(request, "chat.html")

    # --- Helper: panggil Dialogflow detect_intent (SINKRON/blocking gRPC) ---
    # SELALU lewat Dialogflow, baik untuk pertanyaan maupun echo/poll, agar
    # seluruh percakapan TEREKAM (mis. ke Avaya via konektor CCAI).
    def _detect_df(session_id, text):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CONFIG["camerad_service_account_file"]
        project_id = CONFIG.get("camerad_project_id")
        session_client = dialogflow.SessionsClient()
        session = session_client.session_path(project_id, session_id)
        text_input = dialogflow.TextInput(text=text, language_code="id")
        query_input = dialogflow.QueryInput(text=text_input)
        return session_client.detect_intent(
            request={"session": session, "query_input": query_input}
        )

    def _is_fallback_reply(reply):
        """Fallback bila balasan kosong / persis kalimat fallback resmi."""
        fb = ""
        try:
            if dfdb is not None:
                fb = (dfdb.get_config().get("fallback") or "").strip()
        except Exception:
            fb = ""
        r = (reply or "").strip()
        return (not r) or (bool(fb) and r == fb)

    # --- 2. Endpoint pesan: menangani PERTANYAAN maupun POLL (echo) ---
    # Frontend memanggil endpoint yang sama:
    #   - kirim pertanyaan  : { text: "<pertanyaan user>", session_id }
    #   - poll (echo)       : { text: SENTINEL_POLL, session_id }
    # Hasil jawaban + durasi backend dibaca dari job-store (sumber kebenaran),
    # sedangkan detect_intent tetap dijalankan agar giliran terekam di Avaya.
    #
    # Bila giliran ini memicu intent handoff (mis. user ketik 1500200 ->
    # "System_System_Hubungi Agent Connector"), respons menyertakan
    # handoff=True + agent_queue (nilai $agent_kring_pajak) + payload enrichment
    # agar frontend beralih ke mode agen — meniru perilaku widget Avaya netlify.
    @app.post("/api/chat/detect")
    async def detect_intent_chat(request: Request):
        data = await request.json()
        text = data.get("text", "")
        session_id = data.get("session_id", str(uuid.uuid4()))
        is_poll = (text == SENTINEL_POLL)

        try:
            response = await run_in_threadpool(_detect_df, session_id, text)
            qr = response.query_result
            reply = qr.fulfillment_text or ""
            intent_name = getattr(qr.intent, "display_name", "") or ""
            confidence = round(
                float(getattr(qr, "intent_detection_confidence", 0.0) or 0.0), 3
            )

            # Info handoff (deteksi minta agen langsung) untuk frontend.
            params = _to_dict(getattr(qr, "parameters", None))
            payloads = _extract_payloads(qr)
            handoff = _is_handoff(intent_name, params, payloads)
            agent_queue = _agent_queue(params, payloads)

            meta = {
                "intent": intent_name,
                "confidence": confidence,
                "handoff": handoff,
                "agent_queue": agent_queue,
                "parameters": params,
                "payload": payloads[0] if payloads else None,
            }

            # Handoff terpicu -> pakai fulfillment intent handoff apa adanya,
            # abaikan job RAG lama (bila ada) agar tidak menampilkan jawaban
            # basi. Frontend akan beralih ke mode agen.
            if handoff and not is_poll:
                return {
                    "reply": reply,
                    "session_id": session_id,
                    "ready": True,
                    "pending": False,
                    "durasi_backend": None,
                    "is_fallback": False,
                    **meta,
                }

            job = dfw.ambil_job(session_id) if dfw else None

            # Jawaban sudah siap (baik fast-path giliran-1 maupun echo).
            if job and job.get("status") == "done":
                return {
                    "reply": job.get("jawaban") or reply,
                    "session_id": session_id,
                    "ready": True,
                    "pending": False,
                    "durasi_backend": job.get("durasi_backend"),
                    "is_fallback": bool(job.get("is_fallback")),
                    **meta,
                }

            # Masih dihitung di latar belakang -> minta frontend polling.
            if job and job.get("status") == "pending":
                return {
                    "reply": reply or "",
                    "session_id": session_id,
                    "ready": False,
                    "pending": True,
                    "is_fallback": False,
                    **meta,
                }

            # Tidak ada job. Untuk poll -> anggap masih pending (frontend akan
            # berhenti setelah batas percobaan). Untuk pertanyaan (mis. webhook
            # nonaktif / balasan statis) -> pakai balasan Dialogflow apa adanya.
            if is_poll:
                return {"session_id": session_id, "ready": False, "pending": True}
            return {
                "reply": reply,
                "session_id": session_id,
                "ready": True,
                "pending": False,
                "durasi_backend": None,
                "is_fallback": _is_fallback_reply(reply),
                **meta,
            }
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
