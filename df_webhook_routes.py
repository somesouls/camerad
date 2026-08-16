# -*- coding: utf-8 -*-
"""df_webhook_routes.py — Webhook Dialogflow ES untuk ChatBot Kring Pajak.

OPSI B (ECHO/REPLAY) — supaya jawaban RAG+LLM yang bisa >5 dtk TETAP terkirim
lewat Dialogflow (agar terekam, mis. Avaya) tanpa melanggar batas ~5 dtk ES:

  Giliran 1 (pertanyaan): webhook memulai komputasi RAG di THREAD LATAR
  BELAKANG lalu menyimpannya di job-store per-sesi. Ia menunggu s.d. `deadline`
  (default 4,5 dtk). Bila jawaban keburu siap -> dikirim langsung (giliran
  tunggal, cocok untuk mode cepat). Bila belum -> webhook membalas ACK singkat
  ("mohon tunggu"), komputasi dibiarkan lanjut di latar belakang.

  Giliran 2 (echo/poll): frontend mengirim teks SENTINEL_POLL lewat Dialogflow
  (jatuh ke Default Fallback Intent -> webhook). Webhook TIDAK menghitung ulang,
  hanya MENGAMBIL hasil dari job-store: bila sudah siap -> kirim jawaban penuh
  (terekam di Avaya); bila belum -> ACK lagi.

UKUR WAKTU
  durasi_backend = waktu MURNI di backend: sejak pesan diterima webhook sampai
  jawaban RAG siap dikirim. TIDAK termasuk lalu lintas frontend/Dialogflow
  maupun jeda polling. Dicatat ke log server + diekspos via ambil_job() ke
  chat_frontend_routes untuk ditampilkan di UI.

CATATAN PRODUKSI
  Poll berbasis teks sentinel ini akan muncul sebagai giliran user di transkrip
  Avaya. Untuk produksi sebaiknya diganti Dialogflow EVENT khusus (intent
  terpisah, webhook aktif, tanpa respons statis) agar transkrip bersih.

KEAMANAN
  Endpoint /api/df/webhook publik (server-ke-server Google) -> DILINDUNGI token
  rahasia (header 'X-Camerad-Token: <token>' ATAU query '?token=<token>').

Endpoint:
  POST /api/df/webhook               -> fulfillment Dialogflow ES (publik, token)
  GET  /df-webhook                   -> (admin) halaman pengaturan
  GET  /api/df/webhook/config        -> (admin) muat konfigurasi + URL webhook
  POST /api/df/webhook/config/save   -> (admin) simpan konfigurasi
  POST /api/df/webhook/config/rotate -> (admin) ganti token rahasia
  POST /api/df/webhook/test          -> (admin) uji fast-path (tanpa Dialogflow)
"""
import os
import time
import threading

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag_engine
import df_webhook_db as dfdb
import agent_log_db as aldb


# Teks penanda giliran "echo/poll" (Opsi B). Harus SAMA dengan yang dikirim
# frontend (chat.html / chat_frontend_routes).
SENTINEL_POLL = "__CAMERAD_POLL__"

# Balasan sementara saat jawaban belum siap (giliran 1 lambat / poll pending).
ACK_TEXT = "⏳ Mohon tunggu sebentar, jawaban sedang saya siapkan…"


# ---- Riwayat per-session (in-memory, dengan TTL) ----------------------------
_HIST = {}
_HIST_LOCK = threading.Lock()
_HIST_TTL = 30 * 60  # 30 menit


def _hist_get(session, turns):
    if not session or turns <= 0:
        return []
    now = time.time()
    with _HIST_LOCK:
        ent = _HIST.get(session)
        if not ent:
            return []
        ts, items = ent
        if now - ts > _HIST_TTL:
            _HIST.pop(session, None)
            return []
        return list(items[-(turns * 2):])


def _hist_add(session, turns, question, answer):
    if not session or turns <= 0:
        return
    now = time.time()
    with _HIST_LOCK:
        ent = _HIST.get(session)
        items = list(ent[1]) if ent else []
        items += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ]
        items = items[-(turns * 2):]
        _HIST[session] = (now, items)
        stale = [k for k, v in _HIST.items() if now - v[0] > _HIST_TTL]
        for k in stale:
            _HIST.pop(k, None)


# ---- Job-store Opsi B (echo/replay) -----------------------------------------
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBS_TTL = 10 * 60  # 10 menit


def _session_key(session_path):
    s = (session_path or "").strip()
    if not s:
        return "default"
    return s.rsplit("/", 1)[-1] or "default"


def _bersihkan_job_lama(now_mono):
    stale = [k for k, v in _JOBS.items()
             if now_mono - v.get("t_diterima", now_mono) > _JOBS_TTL]
    for k in stale:
        _JOBS.pop(k, None)


def ambil_job(session_key):
    """Dibaca chat_frontend_routes (proses sama) untuk status/hasil + durasi."""
    with _JOBS_LOCK:
        job = _JOBS.get((session_key or "").strip())
        if not job:
            return None
        return {
            "status": job.get("status"),
            "jawaban": job.get("jawaban") or "",
            "is_fallback": bool(job.get("is_fallback")),
            "durasi_backend": job.get("durasi_backend"),
            "pertanyaan": job.get("pertanyaan") or "",
            "intent": job.get("intent") or "",
            "topik": job.get("topik") or "",
        }


def _kerja_rag(skey, question, history, profil, turns, fallback, intent=""):
    """Dijalankan di thread latar belakang: hitung RAG lalu simpan ke job."""
    jawaban = ""
    is_fb = False
    topik = ""
    sources = []
    grounded = False
    try:
        res = rag_engine.jawab_chat(question, history, profil)
        ans = (res or {}).get("answer") or ""
        # 'topik' = domain/kategori yang disimpulkan mesin RAG (mis. 'intent',
        # 'peraturan', 'aplikasi', 'umum'). Berguna untuk observabilitas log.
        topik = str((res or {}).get("domain") or "").strip()
        sources = (res or {}).get("sources") or []
        grounded = bool((res or {}).get("grounded"))
        if res and res.get("ok") and ans.strip():
            jawaban = ans
            _hist_add(skey, turns, question, jawaban)
        else:
            jawaban = fallback
            is_fb = True
    except Exception:
        jawaban = fallback
        is_fb = True

    # Catat interaksi chatbot (profil 'chatbot') agar bisa direview lewat menu
    # Konfigurasi/Evaluasi Chatbot via agent_log_db.list_logs(profil='chatbot').
    # Chat Dialogflow tidak lewat jalur /api/rag/agent, jadi dicatat di sini.
    try:
        aldb.log_chat(skey, "wajib_pajak", profil, question, jawaban,
                      [] if is_fb else sources,
                      grounded and not is_fb, topik)
    except Exception:
        pass

    t_selesai = time.monotonic()
    durasi = None
    ev = None
    with _JOBS_LOCK:
        job = _JOBS.get(skey)
        if job is not None:
            job["jawaban"] = jawaban
            job["is_fallback"] = is_fb
            job["status"] = "done"
            job["t_selesai"] = t_selesai
            job["topik"] = topik
            durasi = round(t_selesai - job["t_diterima"], 3)
            job["durasi_backend"] = durasi
            ev = job.get("ev")
    try:
        print(f"[Opsi B] sesi={skey} durasi_backend={durasi}s fallback={is_fb} "
              f"intent={intent or '-'} topik={topik or '-'} "
              f"(pesan masuk webhook -> jawaban siap)")
    except Exception:
        pass
    if ev is not None:
        ev.set()


def _extract_query(payload):
    """Ambil (queryText, session, languageCode, intentName) dari body DF ES."""
    if not isinstance(payload, dict):
        return "", "", "", ""
    qr = payload.get("queryResult") or {}
    q = (qr.get("queryText") or "").strip()
    lang = (qr.get("languageCode") or "").strip()
    intent = ((qr.get("intent") or {}).get("displayName") or "").strip()
    session = (payload.get("session") or "").strip()
    return q, session, lang, intent


def _df_reply(text):
    """Bungkus jawaban ke format fulfillment Dialogflow ES."""
    text = text or ""
    return {
        "fulfillmentText": text,
        "fulfillmentMessages": [{"text": {"text": [text]}}],
        "source": "camerad-kringpajak",
    }


def _public_url(request):
    """URL publik endpoint webhook untuk ditampilkan di halaman admin.

    Di balik Cloudflare tunnel / reverse-proxy, request.base_url sering berupa
    alamat INTERNAL (mis. http://0.0.0.0:8080) sehingga URL yang ditampilkan
    salah bila dicopy ke konsol Dialogflow. Utamakan basis publik dari env
    CAMERAD_PUBLIC_BASE; bila kosong dan base_url terlihat internal, pakai
    domain publik bawaan (https://api.agenthebat.com).
    """
    base = (os.environ.get("CAMERAD_PUBLIC_BASE") or "").strip().rstrip("/")
    if not base:
        rb = str(request.base_url).rstrip("/")
        if any(h in rb for h in ("0.0.0.0", "127.0.0.1", "localhost")):
            base = "https://api.agenthebat.com"
        else:
            base = rb
    return base + "/api/df/webhook"


def register(app):

    async def _body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    async def api_df_webhook(request: Request):
        cfg = dfdb.get_config()

        # --- verifikasi token rahasia ---
        tok = (request.headers.get("x-camerad-token")
               or request.query_params.get("token")
               or request.query_params.get("key") or "").strip()
        if not cfg.get("token") or tok != cfg["token"]:
            return JSONResponse(_df_reply("Maaf, layanan sedang tidak tersedia."),
                                status_code=403)

        if not cfg.get("aktif"):
            return JSONResponse(_df_reply(cfg.get("fallback") or ""), status_code=200)

        payload = await _body(request)
        question, session, lang, intent = _extract_query(payload)
        if not question:
            return JSONResponse(_df_reply(cfg.get("fallback") or ""), status_code=200)

        skey = _session_key(session)
        profil = cfg.get("profil") or "chatbot"
        deadline = max(0.5, float(cfg.get("deadline_ms") or 4500) / 1000.0)
        turns = cfg["riwayat_turn"] if cfg.get("pakai_riwayat") else 0
        fallback = cfg.get("fallback") or ""

        # ---- Giliran ECHO/POLL: ambil hasil, JANGAN hitung ulang ----
        if question == SENTINEL_POLL:
            with _JOBS_LOCK:
                job = _JOBS.get(skey)
                status = job.get("status") if job else None
                jawaban = job.get("jawaban") if job else ""
            if status == "done":
                return JSONResponse(_df_reply(jawaban or fallback), status_code=200)
            return JSONResponse(_df_reply(ACK_TEXT), status_code=200)

        # ---- Giliran PERTANYAAN BARU: mulai komputasi latar belakang ----
        now = time.monotonic()
        ev = threading.Event()
        with _JOBS_LOCK:
            _bersihkan_job_lama(now)
            _JOBS[skey] = {
                "status": "pending",
                "pertanyaan": question,
                "jawaban": "",
                "is_fallback": False,
                "t_diterima": now,
                "t_selesai": None,
                "durasi_backend": None,
                "intent": intent,
                "topik": "",
                "ev": ev,
            }
        history = _hist_get(skey, turns)
        threading.Thread(
            target=_kerja_rag,
            args=(skey, question, history, profil, turns, fallback, intent),
            daemon=True,
        ).start()

        # Fast-path: tunggu s.d. deadline (di threadpool agar event loop bebas
        # melayani callback lain). Siap -> kirim langsung; belum -> ACK.
        selesai = await run_in_threadpool(ev.wait, deadline)
        if selesai:
            with _JOBS_LOCK:
                job = _JOBS.get(skey) or {}
                jawaban = job.get("jawaban") or ""
            if jawaban.strip():
                return JSONResponse(_df_reply(jawaban), status_code=200)
            return JSONResponse(_df_reply(fallback), status_code=200)
        return JSONResponse(_df_reply(ACK_TEXT), status_code=200)

    async def page_df_webhook(request: Request):
        return render_page(request, "df_webhook.html", "df_webhook")

    async def api_df_webhook_config(request: Request):
        cfg = dfdb.get_config()
        return JSONResponse({"ok": True, "config": cfg,
                             "webhook_url": _public_url(request)})

    async def api_df_webhook_save(request: Request):
        body = await _body(request)
        cfg = dfdb.save_config(body)
        return JSONResponse({"ok": True, "config": cfg,
                             "webhook_url": _public_url(request)})

    async def api_df_webhook_rotate(request: Request):
        cfg = dfdb.rotate_token()
        return JSONResponse({"ok": True, "config": cfg,
                             "webhook_url": _public_url(request)})

    async def api_df_webhook_test(request: Request):
        import asyncio
        body = await _body(request)
        question = (body.get("question") or "").strip()
        if not question:
            return JSONResponse({"ok": False, "error": "Pertanyaan kosong."},
                                status_code=400)
        cfg = dfdb.get_config()
        profil = (body.get("profil") or cfg.get("profil") or "chatbot").strip()
        deadline = max(0.5, float(cfg.get("deadline_ms") or 4500) / 1000.0)
        t0 = time.time()
        timed_out = False
        answer = ""
        ok = False
        err = ""
        try:
            res = await asyncio.wait_for(
                run_in_threadpool(rag_engine.jawab_chat, question, [], profil),
                timeout=deadline,
            )
            ok = bool(res and res.get("ok"))
            answer = (res or {}).get("answer") or ""
        except asyncio.TimeoutError:
            timed_out = True
        except Exception as e:
            err = str(e)
        elapsed = round(time.time() - t0, 3)
        answered = ok and not timed_out and bool(answer.strip())
        return JSONResponse({
            "ok": True,
            "answered": answered,
            "timed_out": timed_out,
            "elapsed_s": elapsed,
            "deadline_s": deadline,
            "answer": answer if answered else (cfg.get("fallback") or ""),
            "used_fallback": not answered,
            "error": err,
        })

    app.add_api_route("/api/df/webhook", api_df_webhook, methods=["POST"])
    app.add_api_route("/df-webhook", page_df_webhook, methods=["GET"])
    app.add_api_route("/api/df/webhook/config", api_df_webhook_config, methods=["GET"])
    app.add_api_route("/api/df/webhook/config/save", api_df_webhook_save, methods=["POST"])
    app.add_api_route("/api/df/webhook/config/rotate", api_df_webhook_rotate, methods=["POST"])
    app.add_api_route("/api/df/webhook/test", api_df_webhook_test, methods=["POST"])
