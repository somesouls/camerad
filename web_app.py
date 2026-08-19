# -*- coding: utf-8 -*-
"""
web_app.py  —  Frontend Pipeline Dialogflow + Avaya (Step 1..16)

PORT dari index.php (PHP) ke FastAPI (Python). Struktur, cara kerja, dan
TAMPILAN identik: HTML/CSS/JS dipakai ulang apa adanya dari templates/index.html.
Hanya framework server yang berubah (PHP -> Python/FastAPI).

- UI disajikan di GET  /
- Semua aksi memakai kontrak yang sama: /?action=<aksi>&run=<run> (GET/POST)
- Google auth service-account -> access token (pakai google-auth)
- Proses berat (SBERT/reranker/NLI/QA/LLM) tetap di backend FastAPI
  (llm_fix_final_combined.py) yang dipanggil via HTTP di 127.0.0.1:8000.

Jalankan:  uvicorn web_app:app --host 0.0.0.0 --port 8080
     atau:  python web_app.py
"""
import os
import re
import io
import csv
import json
import time
import zipfile
import datetime as _dt
from typing import Optional

import requests
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response, PlainTextResponse, RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool
from urllib.parse import quote as _quote

import llm_client
import users_db as usr
import analytics_db as adb
import avaya_db as avdb
import avaya_client as avc
import threading as _threading
import uuid as _uuid
import ingest
import glossary_db as gdb
import disambig_db as ddb
import intentmap_db as imdb
import knowledge_ctx as kctx  # gabungan konteks 3 pustaka utk prompt
import pustaka_stats as pstats  # statistik pemakaian pustaka
import intent_describe as idesc  # job deskripsi AI utk katalog intent
import pii_mask  # masking PII sebelum teks dikirim ke LLM (Fase A)

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- Fondasi bersama dipindah ke app_core.py (migrasi bertahap, langkah 1) ---
from app_core import app, CONFIG, XLSX_MIME, BASE_DIR, render_page, _load_html



def start_scheduler():
    """Penjadwal ingest harian (default 08:00 Asia/Jakarta untuk data H-1).
    Non-aktifkan dengan PIPELINE_SCHEDULER=0."""
    if (os.environ.get("PIPELINE_SCHEDULER", "1") or "1").strip() == "0":
        print("[scheduler] dimatikan (PIPELINE_SCHEDULER=0).", flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print("[scheduler] APScheduler belum terpasang, penjadwal dilewati:", e, flush=True)
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Jakarta")
    except Exception:
        tz = None
    hour = int(os.environ.get("PIPELINE_INGEST_HOUR", "8"))
    minute = int(os.environ.get("PIPELINE_INGEST_MINUTE", "0"))
    ing_lang = os.environ.get("PIPELINE_INGEST_LANG", "id")

    def _job():
        try:
            y = ingest._yesterday()
            ingest.ingest_range(y, y, lang=ing_lang, verbose=False)
            print("[scheduler] ingest harian selesai untuk %s" % y, flush=True)
        except Exception as e:
            print("[scheduler] ingest gagal:", e, flush=True)

    sch = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
    sch.add_job(_job, "cron", hour=hour, minute=minute, id="daily_ingest",
                replace_existing=True)
    sch.start()
    print("[scheduler] ingest harian aktif jam %02d:%02d Asia/Jakarta." % (hour, minute),
          flush=True)
    return sch






# =============================================================
# Studio Dokumen (Epik C) — daftarkan route dari modul terpisah
# =============================================================
# --- Rute auth & sistem dipindah ke modul terpisah (migrasi langkah 2) ---
import auth_routes
auth_routes.register(app)
import system_routes
system_routes.register(app)

# --- Rute pustaka & lifecycle dipindah ke modul terpisah (migrasi langkah 3) ---
import pustaka_routes
pustaka_routes.register(app)
import lifecycle_routes
lifecycle_routes.register(app)

# --- Rute analytics/deflection, kelola-data, dan tanya-AI dipindah ke modul terpisah (migrasi langkah 4) ---
import analytics_routes
analytics_routes.register(app)
import data_routes
data_routes.register(app)
import knowledge_routes
knowledge_routes.register(app)

# --- Rute AWE Avaya dipindah ke modul terpisah (migrasi langkah 5) ---
# --- Pipeline studio (Dialogflow+Avaya) dipindah ke modul terpisah (migrasi langkah 6) ---
import pipeline_routes
pipeline_routes.register(app)

# --- Perbaikan Step 9 (Analisis Manual MKTA): monkey-patch pipeline_routes.step9_save
#     agar menerima format edit dari frontend (objek map) & membawa data Step 8.
#     WAJIB diimpor SETELAH pipeline_routes selesai dimuat. ---
import step9_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Perbaikan Step 10 (Laporan LM & Pembaruan): monkey-patch pipeline_routes.step10_build
#     agar memakai format baru (NAMA PENYUSUN + TGL Penyusunan) & menggabungkan
#     baris TINDAK LANJUT dari Fallback (Step 6) + MKTA (Step 9).
#     WAJIB diimpor SETELAH pipeline_routes selesai dimuat. ---
import step10_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Poin #1 arsitektur RAG final — "successor-tracing" peraturan:
#     monkey-patch rag_engine._ctx_peraturan agar bila kandidat termirip
#     berstatus dicabut/diubah, mesin menelusuri peraturan pengganti yang
#     berlaku dan menyisipkan catatan status hukum ke konteks. Patch mengimpor
#     rag_engine sendiri & memperbarui _DISPATCH, jadi aman diimpor di sini. ---
import rag_successor_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Tahap 5 (reranker + query rewriting): bungkus peraturan_db.search agar
#     (a) memperluas query dengan kamus sinonim + rewriting AI (peraturan/pasal),
#     (b) mengurutkan ulang kandidat dengan cross-encoder reranker. Dipasang
#     SEBELUM rag_calibration_patch agar gerbang cosine tetap menilai query ASLI
#     (wrapper menerima query asli dari gate, memperluas HANYA utk retrieval,
#     lalu rerank pakai query asli). Fail-open bila modul/model tak tersedia. ---
import rag_rerank_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Point 3 (kalibrasi ambang cosine): aktifkan gerbang retrieval berbasis
#     ambang cosine (rag_calibration_patch) — menyaring pasal peraturan & sumber
#     intent yang kemiripan semantiknya di bawah ambang aktif (min_cos). Ambang
#     produksi dari env RAG_MIN_COS (0 = mati); sweep /rag-eval men-set per-run.
#     Fail-open bila model/vektor tak tersedia. WAJIB setelah rag_successor_patch
#     agar ikut membungkus dispatch peraturan versi successor. ---
import rag_calibration_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 2 (sinyal domain hukum di ranking + filter temporal as-of):
#     membungkus peraturan_db.search versi terakhir (gate -> rerank -> hybrid)
#     agar kekuatan_hukum/recency/entitas/definisi ikut menentukan urutan akhir,
#     dan query bertahun ("... tahun 2019") difilter as-of. WAJIB setelah
#     rag_calibration_patch agar membungkus rantai terakhir. ---
import rag_domain_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Tahap 3 (guardrail grounding jawaban): monkey-patch rag_engine.answer agar
#     (a) membuang/menormalkan tautan tidak resmi/pemendek (t.co/x.com/bit.ly)
#     dari body jawaban, dan (b) memaksa abstain bila jawaban memuat rujukan
#     hukum (PMK/PER/PP/UU + nomor) yang tidak terdukung konteks retrieval.
#     Fail-open. WAJIB setelah rag_successor_patch & rag_calibration_patch agar
#     membungkus versi answer/_render_prompt terakhir. ---
import rag_grounding_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Pembersihan knowledge AWE: monkey-patch rag_engine._ctx_awe agar retrieval
#     Percakapan AWE MEMBUANG giliran Bot / CCAI dan MENGABAIKAN percakapan
#     full-bot (kolom Agent = "Chatbot, Google"). Hanya giliran pelanggan +
#     agent manusia yang dijadikan konteks. WAJIB setelah rag_grounding_patch. ---
import awe_botfilter_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Perutean layanan (handoff): monkey-patch rag_engine.answer agar bila
#     pertanyaan pengguna cocok dengan intent LAYANAN pada tabel handoff_routing,
#     panduan kanal (mandiri/agent/KPP) disisipkan ke prompt. Pertanyaan
#     informasi murni tetap dijawab RAG biasa. WAJIB setelah rag_grounding_patch
#     & awe_botfilter_patch agar membungkus versi answer terakhir. ---
import handoff_routing_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 1 (pemerataan retrieval): monkey-patch sumber SOP/AWE/Sosmed agar
#     memakai perluasan kamus + skor token TERNORMALISASI (text_norm; stemming
#     Sastrawi bila ada) + rerank cross-encoder. SOP lewat pembungkus
#     sop_db.search (perluasan kamus + AI rewrite); AWE/Sosmed lewat pengganti
#     _DISPATCH (bot-filter AWE dari awe_botfilter_patch DIPERTAHANKAN).
#     WAJIB setelah awe_botfilter_patch & handoff_routing_patch agar membungkus
#     versi terakhir masing-masing sumber. ---
import rag_sources_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 5 (Q2Q): indeks PERTANYAAN historis Sosmed/AWE sebagai vektor
#     (kemiripan pertanyaan, bukan jawaban), lalu tautkan rujukan peraturan
#     yang terdeteksi di jawaban historis ke basis peraturan yang rapi.
#     Membungkus _ctx_awe/_ctx_sosmed versi v16; fail-soft bila qa.db belum
#     dibangun (python phase5_qa_build.py). WAJIB setelah rag_sources_patch. ---
import rag_qa_patch  # noqa: F401  (menerapkan patch saat diimpor)

import awe_routes
awe_routes.register(
    app,
    save_artifact=pipeline_routes.save_artifact,
    load_state=pipeline_routes.load_state,
    save_state=pipeline_routes.save_state,
    Ctx=pipeline_routes.Ctx,
    avaya2_pull_intents=pipeline_routes.avaya2_pull_intents,
    avaya3_start=pipeline_routes.avaya3_start,
    avaya3_fetch=pipeline_routes.avaya3_fetch,
    api_endpoint=pipeline_routes.api_endpoint,
    curl_json_raw=pipeline_routes.curl_json_raw,
)

import awe_assess
awe_assess.register(app)

# --- Rute Tool Sosmed (X/IG/TikTok) dipindah ke modul terpisah ---
import sosmed_routes
sosmed_routes.register(app)

# --- Endpoint ingest untuk ekstensi Camerad X-Scraper (POST /api/sosmed/ingest) ---
import sosmed_ingest
sosmed_ingest.register(app)

# --- Menu RAG (Pilot) "Agent Kring Pajak": chat berbasis 3 basis data internal ---
import rag_routes
rag_routes.register(app)

# --- Chat RAG "Agent Kring Pajak" (halaman utama "/" untuk SEMUA peran) +
#     feedback jempol + log keandalan + kuota harian admin. Berbagi mesin RAG
#     (rag_engine) & profil 'agent', jadi didaftarkan setelah rag_routes. ---
import agent_chat_routes
agent_chat_routes.register(app)

# --- Webhook Dialogflow ES (Point 5): endpoint fulfillment chatbot Kring Pajak
#     dengan fast-path + deadline guard ~4,5 dtk, plus menu admin "Webhook
#     Chatbot" untuk mengatur token/profil/deadline/fallback. Memakai mesin RAG
#     (rag_engine) & app_core.render_page, jadi didaftarkan setelah rag_routes. ---
import df_webhook_routes
df_webhook_routes.register(app)


# --- Endpoint untuk Widget Chat Frontend ---
import chat_frontend_routes
chat_frontend_routes.register(app)


# --- Menu Evaluasi RAG (poin 2): kumpulkan sampel pertanyaan asli (livechat +
#     chatbot), jalankan uji keandalan (LLM-as-judge + hold-out AWE), dan
#     dashboard metrik keandalan/halusinasi + validasi manusia. Khusus admin.
#     Didaftarkan setelah rag_routes karena berbagi mesin & profil RAG. ---
import eval_routes
eval_routes.register(app)

# --- Menu Evaluasi RAG · Chatbot: pengujian KHUSUS profil chatbot, terpisah
#     dari profil agent. Tiga metode: (1) coverage training-phrase top-intent,
#     (2) deflection pertanyaan fallback + riwayat percakapan, (3) uji beban
#     concurrency mesin RAG. Khusus admin (area 'peraturan'). Didaftarkan
#     setelah eval_routes karena berbagi mesin & profil RAG. ---
import eval_chatbot_routes
eval_chatbot_routes.register(app)

# --- Menu Peraturan (sumber resource #5): kelola basis data peraturan perpajakan
#     (migrasi dari repositori jakai). Dipakai juga sebagai sumber grounding RAG. ---
import peraturan_routes
peraturan_routes.register(app)

# --- Menu SOP/Proses Bisnis (sumber grounding RAG #6, tampil "Sumber 5"):
#     ekstrak dokumen (pdf/pptx/docx/txt/html/gambar) -> disimpan permanen. ---
import sop_routes
sop_routes.register(app)

# --- Menu Kamus & Rewriting (Tahap 5): kelola kamus sinonim/istilah pajak +
#     alat uji rewriting otomatis AI (peraturan/pasal). Memakai app_core.render_page
#     & mesin RAG, jadi didaftarkan bersama menu Peraturan/SOP. ---
import rag_kamus_routes
rag_kamus_routes.register(app)

# --- Menu Perutean Layanan (Handoff): kelola tabel handoff_routing TANPA
#     redeploy (intent LAYANAN + kanal mandiri/agent/KPP + frasa pemicu).
#     Perutean diterapkan oleh handoff_routing_patch; halaman ini hanya CRUD.
#     Memakai app_core.render_page, jadi didaftarkan bersama menu chatbot. ---
import handoff_routes
handoff_routes.register(app)

import studio_routes
studio_routes.register(
    app,
    base_dir=BASE_DIR,
    render_page=render_page,
    llm_client=llm_client,
    kctx=kctx,
    xlsx_mime=XLSX_MIME,
)


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8080"))
    os.makedirs(CONFIG["runs_dir"], exist_ok=True)
    start_scheduler()
    shown = "localhost" if host in ("0.0.0.0", "::") else host
    print("=" * 64)
    print(" Dialogflow + Avaya Pipeline (FastAPI) - FRONTEND / UI")
    print(" BUKA DI BROWSER : http://%s:%d/" % (shown, port))
    print(" (JANGAN buka http://0.0.0.0:%d - itu cuma alamat bind, bukan URL)" % port)
    print(" Dari PC lain LAN: http://<IP-PC-INI>:%d/" % port)
    print(" Backend internal (jangan dibuka manual): %s" % CONFIG["local_api_base"])
    print("=" * 64)
    uvicorn.run(app, host=host, port=port)
