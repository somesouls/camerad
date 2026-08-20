# -*- coding: utf-8 -*-
"""app_core.py — Fondasi bersama aplikasi (migrasi bertahap dari web_app.py).

Berisi objek FastAPI `app`, konfigurasi global (CONFIG / XLSX_MIME / BASE_DIR),
template engine, middleware autentikasi, dan helper render halaman.

Modul fitur cukup meng-import dari sini, mis:
    from app_core import app, CONFIG, render_page

CATATAN: app_core TIDAK boleh meng-import web_app (agar tidak circular import).
Langkah 1 dari rencana pemecahan web_app.py.
"""
import os

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import quote as _quote

import db.users_db as usr

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    "project_id": os.environ.get("PIPELINE_PROJECT_ID", "avaya-djp-klipbot-prod"),
    "service_account_file": os.environ.get("PIPELINE_SA_FILE")
    or os.path.join(BASE_DIR, "service-account.json"),
    "camerad_project_id": os.environ.get("CAMERAD_PROJECT_ID", "camerad-mcpg"),
    "camerad_service_account_file": os.environ.get("CAMERAD_SA_FILE")
    or os.path.join(BASE_DIR, "camerad-service-account.json"),
    "google_scope": "https://www.googleapis.com/auth/cloud-platform",
    "qwen_api_key": os.environ.get("PIPELINE_API_KEY", "sam-n8n-secret"),
    "local_api_base": os.environ.get("PIPELINE_API_BASE") or "http://127.0.0.1:8000",
    "force_local_api": os.environ.get("PIPELINE_FORCE_LOCAL", "1") != "0",
    "mkta_chunk": int(os.environ.get("PIPELINE_MKTA_CHUNK", "12")),
    "runs_dir": os.environ.get("PIPELINE_RUNS_DIR") or os.path.join(BASE_DIR, "_runs"),
}

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(title="Camerad Studio")

# Avaya AWE kini dipasang ke proses web utama agar ./start.bat cukup membuka
# satu server (web_app.py:8080). Fail-soft: bila dependency Avaya belum siap,
# route lain tetap boot.
try:
    import avaya.web_bootstrap as avaya_web_bootstrap
    avaya_web_bootstrap.register(app)
except Exception as _avaya_web_exc:
    print("[AVAYA-WEB] bootstrap dilewati:", _avaya_web_exc, flush=True)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Bintang berarti mengizinkan akses dari domain mana saja
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_TEMPLATES = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _load_html(name):
    p = os.path.join(BASE_DIR, "templates", name)
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _page_user_ctx(request):
    token = request.cookies.get("session")
    user = _user_from_token(token) if token else None
    nama = user.get("nama") if user and user.get("nama") else "Analis Pajak"
    role_key = (user.get("role") if user and user.get("role") else "") or ""
    role_lbl = usr.role_label(role_key) if role_key else "Fungsional Penyuluh"
    avatar = (nama[0].upper() if nama else "A")
    return {
        "user_name": nama,
        "user_role": role_lbl,
        "user_role_key": role_key,
        "user_avatar": avatar,
        "user_avatar_img": (user.get("avatar") if user else "") or "",
        "user_username": (user.get("username") if user else "") or "",
        "can_dialogflow": usr.area_allowed(role_key, "dialogflow"),
        "can_awe": usr.area_allowed(role_key, "awe"),
        "can_awe_manage": usr.area_allowed(role_key, "awe_manage"),
        "can_assess": usr.area_allowed(role_key, "assess"),
        "can_users": usr.area_allowed(role_key, "users"),
        "can_sosmed": usr.area_allowed(role_key, "common"),
        "can_sosmed_manage": usr.area_allowed(role_key, "awe_manage"),
        "can_peraturan": usr.area_allowed(role_key, "peraturan"),
        # Kanal chat RAG Agent Kring Pajak (semua peran, termasuk 'agent').
        "can_chat": usr.area_allowed(role_key, "chat"),
        # Peran 'agent' hanya boleh chat + profil (menu lain disembunyikan).
        "is_agent": role_key == "agent",
    }


def render_page(request, template_name, active_page="", extra=None):
    ctx = {"active_page": active_page}
    ctx.update(_page_user_ctx(request))
    if extra:
        ctx.update(extra)
    return _TEMPLATES.TemplateResponse(request, template_name, ctx)


# /api/df/webhook = endpoint fulfillment Dialogflow ES (dipanggil server Google),
# jadi harus publik; keamanannya dijaga oleh token rahasia di df_webhook_routes.
_PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/healthz", "/favicon.ico", "/credit", "/api/df/webhook", "/api/chat/detect", "/livechat"}


def _route_action(method, path):
    if path == "/users" or path.startswith("/api/users"):
        return "admin"
    if (path.startswith("/api/sosmed/import") or path.startswith("/api/sosmed/pull")
            or path == "/api/sosmed/purge" or path == "/api/sosmed/repair"):
        return "ingest"
    if path == "/api/sosmed/status" or path == "/api/sosmed/topik":
        return "edit"
    if path.startswith("/api/awe/assess") or path.startswith("/api/assess"):
        return "assess"
    if path in ("/api/intentmap/approve", "/api/intentmap/describe", "/api/intentmap/describe/start", "/api/intentmap/describe/stop"):
        return "approve"
    if path == "/api/ingest" or path == "/api/ingest-upload":
        return "ingest"
    # Impor/proses batch/status pada menu Peraturan diperlakukan sebagai 'ingest';
    # save/delete di bawah sudah tertangani aturan generik berikutnya.
    if path in ("/api/peraturan/import-html", "/api/peraturan/import-jsonl",
                "/api/peraturan/batch", "/api/peraturan/reindex",
                "/api/peraturan/status"):
        return "ingest"
    # Menu SOP/Proses Bisnis: impor folder, reindex, dan audit = 'ingest'.
    if path in ("/api/sop/batch", "/api/sop/reindex", "/api/sop/audit"):
        return "ingest"
    if path.endswith("/save") or path.endswith("/delete"):
        return "edit"
    return "read"


def _route_area(path):
    # Menu "Webhook Chatbot" (Dialogflow ES) = khusus admin. Endpoint publik
    # /api/df/webhook (fulfillment) sudah ada di _PUBLIC_PATHS; halaman + API
    # konfigurasi (/df-webhook, /api/df/webhook/...) diperlakukan 'peraturan'.
    if path == "/df-webhook" or path.startswith("/api/df/webhook/"):
        return "peraturan"
    if path == "/profil" or path.startswith("/api/profil"):
        return "account"
    # Menu Evaluasi RAG (kumpulkan sampel + uji keandalan) = khusus admin.
    # Path /rag-eval, /rag-eval-chatbot & /api/eval sengaja TIDAK memakai prefix
    # /api/rag agar tidak jatuh ke aturan 'common' di bawah. Ditaruh paling awal.
    if path in ("/rag-eval", "/rag-eval-chatbot") or path.startswith("/api/eval"):
        return "peraturan"
    # Playground RAG (uji sumber/prompt) + Konfigurasi RAG Agent + Konfigurasi
    # RAG Chatbot + kelola profil + kuota harian + review log feedback = khusus
    # admin. Ditaruh sebelum aturan /api/rag generik agar tidak jatuh ke 'common'.
    if (path == "/rag-lab" or path == "/rag-agent" or path == "/rag-chatbot"
            or path.startswith("/api/rag/lab")
            or path.startswith("/api/rag/profile")
            or path.startswith("/api/rag/quota")
            or path.startswith("/api/rag/logs")):
        return "peraturan"
    # Chat RAG Agent Kring Pajak (semua peran, termasuk 'agent') + feedback jempol.
    if (path.startswith("/api/rag/agent")
            or path.startswith("/api/rag/feedback")):
        return "chat"
    if path == "/rag" or path.startswith("/api/rag"):
        return "common"
    if path == "/peraturan" or path.startswith("/api/peraturan"):
        return "peraturan"
    # Menu SOP/Proses Bisnis memakai area akses yang sama dengan Peraturan.
    if path == "/sop" or path.startswith("/api/sop"):
        return "peraturan"
    # Menu Kamus & Rewriting (Tahap 5) memakai area akses Peraturan (admin).
    if path == "/kamus" or path.startswith("/api/kamus"):
        return "peraturan"
    # Menu Perutean Layanan (Handoff) memakai area akses Peraturan (admin).
    if path == "/handoff" or path.startswith("/api/handoff"):
        return "peraturan"
    if path == "/users" or path.startswith("/api/users"):
        return "users"
    if (path == "/awe/kelola" or path.startswith("/api/awe/pull")
            or path.startswith("/api/awe/stage") or path.startswith("/api/awe/process")
            or path.startswith("/api/awe/delete")):
        return "awe_manage"
    if (path == "/awe/penilaian" or path.startswith("/api/awe/assess")
            or path.startswith("/api/assess")):
        return "assess"
    if path == "/awe" or path.startswith("/awe/") or path.startswith("/api/awe"):
        return "awe"
    if (path == "/sosmed/kelola" or path.startswith("/api/sosmed/import")
            or path.startswith("/api/sosmed/pull") or path == "/api/sosmed/purge"
            or path == "/api/sosmed/repair"):
        return "awe_manage"
    if path == "/sosmed" or path.startswith("/sosmed/") or path.startswith("/api/sosmed"):
        return "common"
    # Halaman utama (chat RAG), Studio Dokumen, & API Studio = area 'chat' agar
    # peran 'agent' bisa mengaksesnya; sisa API generik tetap 'common'.
    if path == "/" or path == "/studio" or path.startswith("/api/studio"):
        return "chat"
    if (path.startswith("/api/ask") or path.startswith("/api/config")
            or path.startswith("/api/chat")):
        return "common"
    return "dialogflow"


def _user_from_token(token):
    try:
        c = usr.connect()
        try:
            usr.init_db(c)
            return usr.get_session_user(c, token)
        finally:
            c.close()
    except Exception:
        return None


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)

    user = _user_from_token(request.cookies.get("session"))
    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "login": True, "error": "Sesi berakhir atau belum login."}, status_code=401)
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse("/login?next=" + _quote(nxt, safe=""), status_code=302)

    role = user.get("role")
    if not usr.area_allowed(role, _route_area(path)):
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Akses ditolak untuk peran Anda."}, status_code=403)
        return RedirectResponse("/", status_code=302)

    if not usr.can(role, _route_action(request.method, path)):
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Akses ditolak untuk peran Anda."}, status_code=403)
        return RedirectResponse("/", status_code=302)

    request.state.user = user
    return await call_next(request)
