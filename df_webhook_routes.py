# -*- coding: utf-8 -*-
"""df_webhook_routes.py — Webhook Dialogflow ES untuk ChatBot Kring Pajak.

Endpoint fulfillment yang dipanggil server Dialogflow ES saat sebuah intent
mengaktifkan webhook (mis. intent Default Fallback). Jawaban diambil dari mesin
RAG internal (rag_engine.jawab_chat) dengan grounding ke basis pengetahuan
perpajakan.

FAST-PATH + DEADLINE GUARD
  Dialogflow ES memutus koneksi webhook pada ~5 detik. Karena RAG+LLM bisa lebih
  lama, jawaban dihitung di threadpool dengan batas waktu (default 4,5 dtk).
  Bila lewat batas, endpoint segera membalas kalimat fallback cepat (HTTP 200)
  supaya bot tetap menjawab; komputasi RAG yang masih berjalan dibiarkan selesai
  di latar belakang (hasilnya tidak dipakai untuk giliran ini).

KEAMANAN
  Endpoint /api/df/webhook publik (dipanggil server-ke-server Google), jadi
  DILINDUNGI token rahasia: Dialogflow harus mengirim header
  'X-Camerad-Token: <token>' ATAU query '?token=<token>'. Token dikelola di menu
  "Webhook Chatbot".

Endpoint:
  POST /api/df/webhook               -> fulfillment Dialogflow ES (publik, token)
  GET  /df-webhook                   -> (admin) halaman pengaturan
  GET  /api/df/webhook/config        -> (admin) muat konfigurasi + URL webhook
  POST /api/df/webhook/config/save   -> (admin) simpan konfigurasi
  POST /api/df/webhook/config/rotate -> (admin) ganti token rahasia
  POST /api/df/webhook/test          -> (admin) uji fast-path (tanpa Dialogflow)
"""
import asyncio
import time
import threading

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag_engine
import df_webhook_db as dfdb


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
    base = str(request.base_url).rstrip("/")
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

        profil = cfg.get("profil") or "chatbot"
        deadline = max(0.5, float(cfg.get("deadline_ms") or 4500) / 1000.0)
        turns = cfg["riwayat_turn"] if cfg.get("pakai_riwayat") else 0
        history = _hist_get(session, turns)

        try:
            res = await asyncio.wait_for(
                run_in_threadpool(rag_engine.jawab_chat, question, history, profil),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            # Fast-path: lewat deadline -> balas fallback cepat agar DF tak time out.
            return JSONResponse(_df_reply(cfg.get("fallback") or ""), status_code=200)
        except Exception:
            return JSONResponse(_df_reply(cfg.get("fallback") or ""), status_code=200)

        answer = (res or {}).get("answer") or ""
        if not (res and res.get("ok") and answer.strip()):
            answer = cfg.get("fallback") or ""
        else:
            _hist_add(session, turns, question, answer)
        return JSONResponse(_df_reply(answer), status_code=200)

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
