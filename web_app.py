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

import common.llm_client as llm_client
import db.users_db as usr
import db.analytics_db as adb
import avaya.db as avdb
import avaya.client as avc
import threading as _threading
import uuid as _uuid
import ingest
import knowledge.glossary_db as gdb
import knowledge.disambig_db as ddb
import knowledge.intentmap_db as imdb
import knowledge.ctx as kctx  # gabungan konteks 3 pustaka utk prompt
import knowledge.stats as pstats  # statistik pemakaian pustaka
import common.intent_describe as idesc  # job deskripsi AI utk katalog intent
import common.pii_mask as pii_mask  # masking PII sebelum teks dikirim ke LLM (Fase A)

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


def start_awe_scheduler():
    """Penjadwal tarik+proses data AWE Avaya harian (data H-1), mirip ingest
    Dialogflow. NONAKTIF secara default: aktifkan dengan AWE_SCHEDULER=1 SETELAH
    mengisi kredensial AVAYA_USERNAME/AVAYA_PASSWORD di .env. Logika tarik+proses
    ada di awe.routes.awe_autopull_run() (memakai worker yang sama dgn alur
    manual Kelola Data AWE)."""
    if (os.environ.get("AWE_SCHEDULER", "0") or "0").strip() != "1":
        print("[awe-scheduler] nonaktif (set AWE_SCHEDULER=1 untuk mengaktifkan).", flush=True)
        return None
    if not (os.environ.get("AVAYA_PASSWORD") or "").strip():
        print("[awe-scheduler] AWE_SCHEDULER=1 tetapi AVAYA_PASSWORD kosong; penjadwal dilewati.", flush=True)
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print("[awe-scheduler] APScheduler belum terpasang, penjadwal dilewati:", e, flush=True)
        return None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Jakarta")
    except Exception:
        tz = None
    hour = int(os.environ.get("AWE_INGEST_HOUR", "5"))
    minute = int(os.environ.get("AWE_INGEST_MINUTE", "0"))
    import awe.routes as _awe_routes

    def _job():
        try:
            res = _awe_routes.awe_autopull_run(trigger="scheduler")
            print("[awe-scheduler] auto-pull selesai:",
                  (res or {}).get("message") or (res or {}).get("error"), flush=True)
        except Exception as e:
            print("[awe-scheduler] auto-pull gagal:", e, flush=True)

    sch = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()
    sch.add_job(_job, "cron", hour=hour, minute=minute, id="daily_awe_ingest",
                replace_existing=True, max_instances=1, coalesce=True)
    sch.start()
    print("[awe-scheduler] tarik+proses AWE harian aktif jam %02d:%02d Asia/Jakarta." % (hour, minute),
          flush=True)
    return sch






# =============================================================
# Studio Dokumen (Epik C) — daftarkan route dari modul terpisah
# =============================================================
# --- Rute auth & sistem dipindah ke modul terpisah (migrasi langkah 2) ---
import routes.auth_routes as auth_routes
auth_routes.register(app)
import routes.system_routes as system_routes
system_routes.register(app)

# --- Rute pustaka & lifecycle dipindah ke modul terpisah (migrasi langkah 3) ---
import pustaka.routes as pustaka_routes
pustaka_routes.register(app)
import routes.lifecycle_routes as lifecycle_routes
lifecycle_routes.register(app)

# --- Rute analytics/deflection, kelola-data, dan tanya-AI dipindah ke modul terpisah (migrasi langkah 4) ---
import routes.analytics_routes as analytics_routes
analytics_routes.register(app)
import routes.data_routes as data_routes
data_routes.register(app)
import knowledge.routes as knowledge_routes
knowledge_routes.register(app)

# --- Menu Laporan (Opsi B / Fase 3): ruang kerja laporan AI agentic (membaca
#     lintas database internal via db.registry, read-only, kecuali users) +
#     simpan ke reports.db + ekspor Markdown/PDF. ADITIF & NON-BREAKING;
#     memakai app_core.render_page & knowledge.agentic. ---
import routes.laporan_routes as laporan_routes
laporan_routes.register(app)

# --- Rute AWE Avaya dipindah ke modul terpisah (migrasi langkah 5) ---
# --- Pipeline studio (Dialogflow+Avaya) dipindah ke modul terpisah (migrasi langkah 6) ---
import pipeline.routes as pipeline_routes
pipeline_routes.register(app)

# --- Perbaikan Step 9 (Analisis Manual MKTA): monkey-patch pipeline_routes.step9_save
#     agar menerima format edit dari frontend (objek map) & membawa data Step 8.
#     WAJIB diimpor SETELAH pipeline_routes selesai dimuat. ---
import pipeline.step9_patch as step9_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Perbaikan Step 10 (Laporan LM & Pembaruan): monkey-patch pipeline_routes.step10_build
#     agar memakai format baru (NAMA PENYUSUN + TGL Penyusunan) & menggabungkan
#     baris TINDAK LANJUT dari Fallback (Step 6) + MKTA (Step 9).
#     WAJIB diimpor SETELAH pipeline_routes selesai dimuat. ---
import pipeline.step10_patch as step10_patch  # noqa: F401  (menerapkan patch saat diimpor)
# --- Sinyal analisis Step 6 & 9 (acuan analis) ---
import pipeline.step6_patch          # noqa: F401
import pipeline.step9_signals_patch  # noqa: F401
import pipeline.step6_idtrace_patch  # noqa: F401


# --- Fase 2 (UX Step 6/9): id_trace utk tombol \"mata\" (lihat percakapan penuh) +
#     aksi 'intents' utk kolom pencarian intent di dropdown Step 6/9.
#     step6_patch & step9_signals_patch (sinyal Fase 1) sudah di-chain-import oleh
#     step10_patch; impor eksplisit di sini AMAN (modul di-cache Python, tak
#     dobel-bungkus) dan MENJAMIN urutan: step6_load harus sudah dibungkus
#     step6_patch sebelum step6_idtrace_patch membungkusnya lagi (berantai).
#     WAJIB setelah step10_patch. ---
import pipeline.step6_patch as step6_patch  # noqa: F401  (idempoten; sinyal Step 6)
import pipeline.step9_signals_patch as step9_signals_patch  # noqa: F401  (idempoten; sinyal Step 9)
import pipeline.step6_idtrace_patch as step6_idtrace_patch  # noqa: F401  (id_trace tombol mata Step 6)
import pipeline.intents_patch as intents_patch  # noqa: F401  (aksi 'intents' pencarian intent Step 6/9)

# --- Tahap 1 (Router soft-prior): monkey-patch rag_router.route agar Router v2
#     TIDAK meng-hard-cut sumber untuk kueri peraturan-jelas, melainkan
#     mendemosikan sumber 'tunda' (mis. intent/awe) ke prioritas TERENDAH
#     (soft-prior) sehingga sumber lintas-kategori yang relevan tidak hilang.
#     Env RAG_ROUTER_SOFTPRIOR=1 (default); set 0 untuk kembali ke hard-cut.
#     Fail-open bila modul router tak tersedia. Router berjalan sebelum
#     retrieval, jadi aman diimpor di sini (independen dari patch _ctx_*). ---
import rag.router_softprior_patch as rag_router_softprior_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Poin #1 arsitektur RAG final — \"successor-tracing\" peraturan:
#     monkey-patch rag_engine._ctx_peraturan agar bila kandidat termirip
#     berstatus dicabut/diubah, mesin menelusuri peraturan pengganti yang
#     berlaku dan menyisipkan catatan status hukum ke konteks. Patch mengimpor
#     rag_engine sendiri & memperbarui _DISPATCH, jadi aman diimpor di sini. ---
import rag.successor_patch as rag_successor_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Tahap 5 (reranker + query rewriting): bungkus peraturan_db.search agar
#     (a) memperluas query dengan kamus sinonim + rewriting AI (peraturan/pasal),
#     (b) mengurutkan ulang kandidat dengan cross-encoder reranker. Dipasang
#     SEBELUM rag_calibration_patch agar gerbang cosine tetap menilai query ASLI
#     (wrapper menerima query asli dari gate, memperluas HANYA utk retrieval,
#     lalu rerank pakai query asli). Fail-open bila modul/model tak tersedia. ---
import rag.rerank_patch as rag_rerank_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Point 3 (kalibrasi ambang cosine): aktifkan gerbang retrieval berbasis
#     ambang cosine (rag_calibration_patch) — menyaring pasal peraturan & sumber
#     intent yang kemiripan semantiknya di bawah ambang aktif (min_cos). Ambang
#     produksi dari env RAG_MIN_COS (0 = mati); sweep /rag-eval men-set per-run.
#     Fail-open bila model/vektor tak tersedia. WAJIB setelah rag_successor_patch
#     agar ikut membungkus dispatch peraturan versi successor. ---
import rag.calibration_patch as rag_calibration_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 2 (sinyal domain hukum di ranking + filter temporal as-of):
#     membungkus peraturan_db.search versi terakhir (gate -> rerank -> hybrid)
#     agar kekuatan_hukum/recency/entitas/definisi ikut menentukan urutan akhir,
#     dan query bertahun (\"... tahun 2019\") difilter as-of. WAJIB setelah
#     rag_calibration_patch agar membungkus rantai terakhir. ---
import rag.domain_patch as rag_domain_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 6 (v25): drill-down ketentuan PELAKSANA — bila kandidat teratas
#     peraturan berlevel UU/PERPU/PERPRES/PP (umum), cari dokumen berlevel lebih
#     rendah (PMK/PER/SE, dst.) yang TERVERIFIKASI merujuk nomor induknya
#     (regex multi-format regref atas isi), lalu sertakan sebagai blok
#     \"Ketentuan pelaksana\". WAJIB setelah rag_domain_patch (membungkus
#     _ctx_peraturan versi terakhir). ---
import rag.drilldown_patch as rag_drilldown_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Tahap 4f (fetch sitasi eksplisit nomor+pasal): bila query menyebut nomor
#     peraturan + pasal EKSPLISIT, tarik ISI pasal itu langsung via SQL mentah
#     LINTAS SEMUA STATUS (kebal FTS/vektor/gerbang cosine), tampilkan + penanda
#     status + pointer penerus. Query tanpa sitasi -> passthrough (nol dampak).
#     Knob RAG_CITATION_FETCH per-profil via knob_store. WAJIB PALING AKHIR di
#     antara patch _ctx_peraturan (setelah drilldown) agar jadi lapis TERLUAR
#     dan delegasi jalur normalnya memakai successor+validity+drilldown. ---
import rag.citation_fetch_patch as rag_citation_fetch_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Tahap 3 (guardrail grounding jawaban): monkey-patch rag_engine.answer agar
#     (a) membuang/menormalkan tautan tidak resmi/pemendek (t.co/x.com/bit.ly)
#     dari body jawaban, dan (b) memaksa abstain bila jawaban memuat rujukan
#     hukum (PMK/PER/PP/UU + nomor) yang tidak terdukung konteks retrieval.
#     Fail-open. WAJIB setelah rag_successor_patch & rag_calibration_patch agar
#     membungkus versi answer/_render_prompt terakhir. ---
import rag.grounding_patch as rag_grounding_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Pembersihan knowledge AWE: monkey-patch rag_engine._ctx_awe agar retrieval
#     Percakapan AWE MEMBUANG giliran Bot / CCAI dan MENGABAIKAN percakapan
#     full-bot (kolom Agent = \"Chatbot, Google\"). Hanya giliran pelanggan +
#     agent manusia yang dijadikan konteks. WAJIB setelah rag_grounding_patch. ---
import awe.botfilter_patch as awe_botfilter_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Perutean layanan (handoff): monkey-patch rag_engine.answer agar bila
#     pertanyaan pengguna cocok dengan intent LAYANAN pada tabel handoff_routing,
#     panduan kanal (mandiri/agent/KPP) disisipkan ke prompt. Pertanyaan
#     informasi murni tetap dijawab RAG biasa. WAJIB setelah rag_grounding_patch
#     & awe_botfilter_patch agar membungkus versi answer terakhir. ---
import handoff.routing_patch as handoff_routing_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 1 (pemerataan retrieval): monkey-patch sumber SOP/AWE/Sosmed agar
#     memakai perluasan kamus + skor token TERNORMALISASI (text_norm; stemming
#     Sastrawi bila ada) + rerank cross-encoder. SOP lewat pembungkus
#     sop_db.search (perluasan kamus + AI rewrite); AWE/Sosmed lewat pengganti
#     _DISPATCH (bot-filter AWE dari awe_botfilter_patch DIPERTAHANKAN).
#     WAJIB setelah awe_botfilter_patch & handoff_routing_patch agar membungkus
#     versi terakhir masing-masing sumber. ---
import rag.sources_patch as rag_sources_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- Fase 5 (Q2Q): indeks PERTANYAAN historis Sosmed/AWE sebagai vektor
#     (kemiripan pertanyaan, bukan jawaban), lalu tautkan rujukan peraturan
#     yang terdeteksi di jawaban historis ke basis peraturan yang rapi.
#     Fase 6: hasil Q2Q Sosmed diekspansi dengan tanya-jawab lanjutan dalam
#     utas yang sama (conv_id). Fail-soft bila qa.db belum dibangun
#     (python phase5_qa_build.py). WAJIB setelah rag_sources_patch. ---
import rag.qa_patch as rag_qa_patch  # noqa: F401  (menerapkan patch saat diimpor)

# --- PR A (sitasi inline + filter sumber dipakai): monkey-patch rag_engine agar
#     (a) daftar {{sumber}} untuk LLM DINOMORI, (b) system prompt menuntut
#     penanda [n], (c) answer (lapis TERLUAR) menyaring res["sources"] hanya ke
#     nomor yang BENAR-BENAR dikutip pada jawaban. Gagal-anggun & di-gate env
#     RAG_CITATION_FILTER (default 1). WAJIB PALING AKHIR di antara pembungkus
#     answer (setelah grounding_patch & handoff_routing_patch) agar jadi lapis
#     terluar dan melihat jawaban+sumber final. ---
import rag.citation_filter_patch as rag_citation_filter_patch  # noqa: F401  (menerapkan patch saat diimpor)

import awe.routes as awe_routes
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

import awe.assess as awe_assess
awe_assess.register(app)

# --- Rute Tool Sosmed (X/IG/TikTok) dipindah ke modul terpisah ---
import sosmed.routes as sosmed_routes
sosmed_routes.register(app)

# --- Endpoint ingest untuk ekstensi Camerad X-Scraper (POST /api/sosmed/ingest) ---
import sosmed.ingest as sosmed_ingest
sosmed_ingest.register(app)

# --- Menu RAG (Pilot) \"Agent Kring Pajak\": chat berbasis 3 basis data internal ---
import rag.routes as rag_routes
rag_routes.register(app)

# --- Chat RAG \"Agent Kring Pajak\" (halaman utama \"/\" untuk SEMUA peran) +
#     feedback jempol + log keandalan + kuota harian admin. Berbagi mesin RAG
#     (rag_engine) & profil 'agent', jadi didaftarkan setelah rag_routes. ---
import chat.agent_routes as agent_chat_routes
agent_chat_routes.register(app)

# --- Webhook Dialogflow ES (Point 5): endpoint fulfillment chatbot Kring Pajak
#     dengan fast-path + deadline guard ~4,5 dtk, plus menu admin \"Webhook
#     Chatbot\" untuk mengatur token/profil/deadline/fallback. Memakai mesin RAG
#     (rag_engine) & app_core.render_page, jadi didaftarkan setelah rag_routes. ---
import df_webhook.routes as df_webhook_routes
df_webhook_routes.register(app)


# --- Endpoint untuk Widget Chat Frontend ---
import chat.frontend_routes as chat_frontend_routes
chat_frontend_routes.register(app)


# --- Menu Evaluasi RAG (poin 2): kumpulkan sampel pertanyaan asli (livechat +
#     chatbot), jalankan uji keandalan (LLM-as-judge + hold-out AWE), dan
#     dashboard metrik keandalan/halusinasi + validasi manusia. Khusus admin.
#     Didaftarkan setelah rag_routes karena berbagi mesin & profil RAG. ---
import evaluation.routes as eval_routes
eval_routes.register(app)

# --- Menu Evaluasi RAG · Chatbot: pengujian KHUSUS profil chatbot, terpisah
#     dari profil agent. Tiga metode: (1) coverage training-phrase top-intent,
#     (2) deflection pertanyaan fallback + riwayat percakapan, (3) uji beban
#     concurrency mesin RAG. Khusus admin (area 'peraturan'). Didaftarkan
#     setelah eval_routes karena berbagi mesin & profil RAG. ---
import evaluation.chatbot_routes as eval_chatbot_routes
eval_chatbot_routes.register(app)

# --- Menu RAG vs LoRA (deliverable #3): bandingkan efektivitas & keandalan
#     RAG (retrieval) vs LoRA (fine-tuning) berdampingan, 3 mode per pertanyaan
#     (rag_base / lora / lora_rag). Memakai evaluation.compare + finetune.serve_local.
#     Khusus admin (area 'peraturan'). Didaftarkan setelah eval_chatbot_routes. ---
import evaluation.compare_routes as eval_compare_routes
eval_compare_routes.register(app)

# --- Menu Peraturan (sumber resource #5): kelola basis data peraturan perpajakan
#     (migrasi dari repositori jakai). Dipakai juga sebagai sumber grounding RAG. ---
import peraturan.routes as peraturan_routes
peraturan_routes.register(app)

# --- Menu SOP/Proses Bisnis (sumber grounding RAG #6, tampil \"Sumber 5\"):
#     ekstrak dokumen (pdf/pptx/docx/txt/html/gambar) -> disimpan permanen. ---
import sop.routes as sop_routes
sop_routes.register(app)

# --- Menu Kamus & Rewriting (Tahap 5): kelola kamus sinonim/istilah pajak +
#     alat uji rewriting otomatis AI (peraturan/pasal). Memakai app_core.render_page
#     & mesin RAG, jadi didaftarkan bersama menu Peraturan/SOP. ---
import rag.kamus_routes as rag_kamus_routes
rag_kamus_routes.register(app)

# --- Menu RAG Harness (Tahap 4 #1): panel admin mandiri /rag-harness (Golden ·
#     Gerbang eval · Tambang feedback · Knob per-profil). LANGKAH 3b = daftarkan
#     endpoint READ-ONLY /api/harness/* (knob efektif per-profil, ringkasan
#     golden set, baseline gerbang, laporan eval terbaru). Aksi tulis menyusul.
#     Area admin 'peraturan' diatur di app_core._route_area (langkah 3c).
#     Memakai rag.knob_store + rag.golden_db, jadi didaftarkan bersama menu
#     Peraturan/Kamus. ---
import routes.harness_routes as harness_routes
harness_routes.register(app)

# --- Menu Perutean Layanan (Handoff): kelola tabel handoff_routing TANPA
#     redeploy (intent LAYANAN + kanal mandiri/agent/KPP + frasa pemicu).
#     Perutean diterapkan oleh handoff_routing_patch; halaman ini hanya CRUD.
#     Memakai app_core.render_page, jadi didaftarkan bersama menu chatbot. ---
import handoff.routes as handoff_routes
handoff_routes.register(app)

import routes.studio_routes as studio_routes
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
    start_awe_scheduler()
    shown = "localhost" if host in ("0.0.0.0", "::") else host

    def _lan_ip():
        """Deteksi IP LAN utama (tanpa benar-benar mengirim paket keluar)."""
        import socket
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "IP-PC-INI"
        finally:
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass

    lan_ip = _lan_ip()
    print("=" * 64)
    print(" Dialogflow + Avaya Pipeline (FastAPI) - FRONTEND / UI")
    print(" BUKA DI BROWSER : http://%s:%d/" % (shown, port))
    print(" (JANGAN buka http://0.0.0.0:%d - itu cuma alamat bind, bukan URL)" % port)
    print(" Dari PC lain LAN: http://%s:%d/" % (lan_ip, port))
    print(" (mesin RAG + Avaya AWE jalan di proses ini; tak perlu buka backend terpisah)")
    print("=" * 64)
    uvicorn.run(app, host=host, port=port)
