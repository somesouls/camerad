# -*- coding: utf-8 -*-
"""chat_frontend_routes.py — Endpoint widget Live Chat (/livechat) + jembatan
Dialogflow ES (Opsi B echo/poll) untuk chat.html.

Alur Opsi B:
  - Giliran PERTANYAAN (bukan sentinel): diteruskan ke Dialogflow
    (detect_intent). Intent dengan fulfillment akan memanggil /api/df/webhook
    yang MEMULAI komputasi RAG di latar belakang (job-store per-sesi) & merekam
    percakapan (mis. Avaya). Handoff ke agen langsung juga terdeteksi di sini.
  - Giliran ECHO/POLL (teks == SENTINEL_POLL): frontend menanyakan \"jawaban
    sudah siap?\".

v31 (perbaikan \"looping\" /livechat):
  Sebelumnya SETIAP poll ikut memanggil Dialogflow detect_intent, sehingga tiap
  1,5 dtk memicu SATU callback /api/df/webhook — membuat badai pasangan
  detect+webhook tak berujung (terlihat sebagai \"looping\" di log) dan menodai
  transkrip Avaya dengan teks sentinel. Karena jawaban RAG sudah dihitung &
  disimpan oleh webhook giliran-pertanyaan, POLL kini membaca job-store LANGSUNG
  (in-process, via dfw.ambil_job) TANPA menyentuh Dialogflow. Hasil: satu
  pertanyaan = tepat satu panggilan Dialogflow + satu webhook; deteksi
  \"selesai\" jadi deterministik. Giliran pertanyaan pertama TETAP lewat
  Dialogflow (mulai job + rekam + handoff).
"""
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

# Klien LLM (Azure/OpenAI/Gemini) untuk terjemahan jalur bahasa. Fail-open.
try:
    import llm_client
except Exception:
    llm_client = None

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

# ---- Jalur bahasa: terjemahan "di tepi" (edge translation) ------------------
# Basis pengetahuan + mesin RAG berjalan dalam Bahasa Indonesia sebagai sumber
# kebenaran. Bahasa mengikuti pilihan tombol di chat.html (id/en), disimpan per
# session_id (poll tidak membawa 'lang'). Untuk user yang memilih English:
#   - INPUT  : pertanyaan EN -> ID sebelum detect_intent + RAG (retrieval akurat)
#   - OUTPUT : jawaban ID -> EN sebelum dikirim balik ke frontend
# Profil agent tidak diterjemahkan (agent menerjemahkan sendiri).
TRANSLATE_AKTIF = (
    os.environ.get("CAMERAD_TRANSLATE", "1").strip().lower()
    not in ("0", "false", "no", "")
)
TRANSLATE_MAX_TOKENS = int(
    os.environ.get("CAMERAD_TRANSLATE_MAX_TOKENS", "1200") or "1200"
)

# Peta session_id -> bahasa ("id"/"en") sesuai pilihan user di chat.html.
SESSION_LANG = {}

_LANG_NAMA = {"en": "English", "id": "Indonesian (Bahasa Indonesia)"}


def _sess_lang(session_id, fallback="id"):
    """Bahasa efektif sesi (default 'id')."""
    v = SESSION_LANG.get(session_id) or fallback or "id"
    v = str(v).strip().lower()
    return "en" if v == "en" else "id"


def _translate(text, target):
    """Terjemahkan `text` ke `target` ('en'/'id') via LLM. Fail-open:
    kembalikan teks asli bila translator nonaktif/tak tersedia/gagal."""
    t = (text or "").strip()
    if not t or not TRANSLATE_AKTIF or llm_client is None:
        return text
    if target not in ("en", "id"):
        return text
    # Lewati token yang jelas bukan kalimat (angka murni / sangat pendek),
    # mis. pemicu handoff "1500200".
    if t.isdigit() or len(t) < 2:
        return text
    nama = _LANG_NAMA.get(target, "English")
    system = (
        "You are a professional translator for an Indonesian tax (DJP / Kring "
        "Pajak) chatbot. Translate the user's message into " + nama + ". "
        "Preserve meaning and tone. Keep numbers, currency, dates, tax terms, "
        "regulation codes (e.g. PER-..., PMK-..., UU ...), URLs, emails, and "
        "markdown formatting (**bold**, *italic*, lists, line breaks) exactly. "
        "Do NOT translate or alter any line that starts with '[' - these are "
        "internal reference tags; keep them verbatim. If the text is already in "
        "the target language, return it unchanged. Return ONLY the translated "
        "text, with no quotes, notes, or preamble."
    )
    try:
        out = llm_client.generate(
            [t], max_new_tokens=TRANSLATE_MAX_TOKENS, system=system,
            temperature=0.0,
        )
        res = ((out[0] if out else "") or "").strip()
        return res or text
    except Exception as exc:  # noqa: BLE001
        print(f"[translate] gagal ({target}): {exc}", flush=True)
        return text


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
    # Dipakai HANYA untuk giliran PERTANYAAN (bukan poll). Ini yang memicu
    # fulfillment /api/df/webhook (memulai job RAG) sekaligus merekam giliran
    # ke Avaya via konektor CCAI.
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
    #   - kirim pertanyaan  : { text: "<pertanyaan user>", session_id, lang }
    #   - poll (echo)       : { text: SENTINEL_POLL, session_id }
    #
    # v31: POLL TIDAK lagi memanggil Dialogflow. Ia membaca job-store LANGSUNG
    # (dfw.ambil_job, proses sama) untuk tahu apakah jawaban sudah siap. Ini
    # menghentikan badai callback /api/df/webhook (satu poll = satu webhook)
    # yang membuat /livechat tampak "looping", dan menjaga transkrip Avaya bersih
    # dari teks sentinel. Giliran PERTANYAAN tetap lewat Dialogflow agar webhook
    # memulai komputasi RAG (Opsi B), percakapan terekam, & handoff terdeteksi.
    #
    # JALUR BAHASA (edge translation): bahasa mengikuti tombol di chat.html.
    # Untuk user English: pertanyaan diterjemahkan EN->ID sebelum detect/RAG,
    # dan jawaban diterjemahkan ID->EN sebelum dikirim balik.
    @app.post("/api/chat/detect")
    async def detect_intent_chat(request: Request):
        data = await request.json()
        text = data.get("text", "")
        session_id = data.get("session_id", str(uuid.uuid4()))
        is_poll = (text == SENTINEL_POLL)

        # Bahasa mengikuti tombol di chat.html; poll tak membawa 'lang' sehingga
        # disimpan pada giliran pertama (per session_id).
        lang_in = (data.get("lang") or "").strip().lower()
        if not is_poll and lang_in:
            SESSION_LANG[session_id] = "en" if lang_in == "en" else "id"
        eff_lang = _sess_lang(session_id)

        async def _out(resp):
            """Terjemahkan reply ID -> EN sebelum dikirim (hanya user English)."""
            if eff_lang == "en" and isinstance(resp, dict) and resp.get("reply"):
                resp["reply"] = await run_in_threadpool(
                    _translate, resp["reply"], "en"
                )
            return resp

        # =================================================================
        # Giliran ECHO/POLL (v31): baca job-store LANGSUNG, TANPA Dialogflow.
        # =================================================================
        if is_poll:
            job = dfw.ambil_job(session_id) if dfw else None
            if job and job.get("status") == "done":
                return await _out({
                    "reply": job.get("jawaban") or "",
                    "session_id": session_id,
                    "ready": True,
                    "pending": False,
                    "durasi_backend": job.get("durasi_backend"),
                    "is_fallback": bool(job.get("is_fallback")),
                })
            # Belum ada job / masih dihitung -> minta frontend lanjut polling.
            return {"session_id": session_id, "ready": False, "pending": True}

        # =================================================================
        # Giliran PERTANYAAN BARU: lewat Dialogflow (mulai job via webhook +
        # rekam percakapan + deteksi handoff).
        # =================================================================
        try:
            # INPUT: user English -> terjemahkan pertanyaan ke ID agar intent
            # matching + retrieval RAG bekerja pada konten Indonesia.
            teks_df = text
            if eff_lang == "en" and text and not str(text).isdigit():
                teks_df = await run_in_threadpool(_translate, text, "id")

            response = await run_in_threadpool(_detect_df, session_id, teks_df)
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
            # abaikan job RAG lama (bila ada). Teks connector TIDAK diterjemahkan
            # (frontend menyembunyikannya).
            if handoff:
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

            # Jawaban sudah siap (fast-path giliran-1 selesai dalam deadline).
            if job and job.get("status") == "done":
                return await _out({
                    "reply": job.get("jawaban") or reply,
                    "session_id": session_id,
                    "ready": True,
                    "pending": False,
                    "durasi_backend": job.get("durasi_backend"),
                    "is_fallback": bool(job.get("is_fallback")),
                    **meta,
                })

            # Masih dihitung di latar belakang -> minta frontend polling. Reply
            # interim tidak ditampilkan frontend, jadi tak perlu diterjemahkan.
            if job and job.get("status") == "pending":
                return {
                    "reply": reply or "",
                    "session_id": session_id,
                    "ready": False,
                    "pending": True,
                    "is_fallback": False,
                    **meta,
                }

            # Tidak ada job (webhook nonaktif / balasan statis) -> pakai balasan
            # Dialogflow apa adanya.
            return await _out({
                "reply": reply,
                "session_id": session_id,
                "ready": True,
                "pending": False,
                "durasi_backend": None,
                "is_fallback": _is_fallback_reply(reply),
                **meta,
            })
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})
