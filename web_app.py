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
