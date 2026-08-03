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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    "project_id": os.environ.get("PIPELINE_PROJECT_ID", "avaya-djp-klipbot-prod"),
    "service_account_file": os.environ.get("PIPELINE_SA_FILE")
    or os.path.join(BASE_DIR, "service-account.json"),
    "google_scope": "https://www.googleapis.com/auth/cloud-platform",
    "qwen_api_key": os.environ.get("PIPELINE_API_KEY", "sam-n8n-secret"),
    "local_api_base": os.environ.get("PIPELINE_API_BASE") or "http://127.0.0.1:8000",
    "force_local_api": os.environ.get("PIPELINE_FORCE_LOCAL", "1") != "0",
    "mkta_chunk": int(os.environ.get("PIPELINE_MKTA_CHUNK", "12")),
    "runs_dir": os.environ.get("PIPELINE_RUNS_DIR") or os.path.join(BASE_DIR, "_runs"),
}

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(title="Camerad Studio")

_TEMPLATES = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _page_user_ctx(request):
    token = request.cookies.get("session")
    user = _user_from_token(token) if token else None
    nama = user.get("nama") if user and user.get("nama") else "Analis Pajak"
    role = user.get("role") if user and user.get("role") else "Fungsional Penyuluh"
    avatar = (nama[0].upper() if nama else "A")
    return {"user_name": nama, "user_role": role, "user_avatar": avatar}


def render_page(request, template_name, active_page=""):
    ctx = {"active_page": active_page}
    ctx.update(_page_user_ctx(request))
    return _TEMPLATES.TemplateResponse(request, template_name, ctx)

_PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/healthz", "/favicon.ico"}


def _route_action(method, path):
    if path == "/users" or path.startswith("/api/users"):
        return "admin"
    if path in ("/api/intentmap/approve", "/api/intentmap/describe", "/api/intentmap/describe/start", "/api/intentmap/describe/stop"):
        return "approve"
    if path == "/api/ingest" or path == "/api/ingest-upload":
        return "ingest"
    if path.endswith("/save") or path.endswith("/delete"):
        return "edit"
    return "read"


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

    if not usr.can(user.get("role"), _route_action(request.method, path)):
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "error": "Akses ditolak untuk peran Anda."}, status_code=403)
        return RedirectResponse("/", status_code=302)

    request.state.user = user
    return await call_next(request)


@app.get("/login")
async def login_page():
    return HTMLResponse(_load_html("login.html"))


@app.get("/users")
async def users_page(request: Request):
    return render_page(request, "users.html", "users")


@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    def _run():
        c = usr.connect()
        try:
            usr.init_db(c)
            u = usr.authenticate(c, username, password)
            if not u:
                return None
            return {"user": u, "token": usr.create_session(c, u["id"])}
        finally:
            c.close()

    res = await run_in_threadpool(_run)
    if not res:
        return JSONResponse({"ok": False, "error": "Username atau sandi salah, atau akun nonaktif."}, status_code=401)

    resp = JSONResponse({
        "ok": True,
        "user": {
            "username": res["user"]["username"],
            "role": res["user"]["role"],
            "nama": res["user"].get("nama", ""),
        },
    })
    resp.set_cookie("session", res["token"], httponly=True, samesite="lax", max_age=usr.session_ttl(), path="/")
    return resp


@app.get("/api/logout")
async def api_logout(request: Request):
    token = request.cookies.get("session")

    def _run():
        c = usr.connect()
        try:
            usr.init_db(c)
            usr.delete_session(c, token)
        finally:
            c.close()

    try:
        await run_in_threadpool(_run)
    except Exception:
        pass

    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session", path="/")
    return resp


@app.get("/api/users")
async def api_users_list(request: Request):
    def _run():
        c = usr.connect()
        try:
            usr.init_db(c)
            return usr.list_users(c)
        finally:
            c.close()

    try:
        users = await run_in_threadpool(_run)
        me = getattr(request.state, "user", None) or {}
        return JSONResponse({
            "ok": True,
            "users": users,
            "me": {"username": me.get("username"), "role": me.get("role")},
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/users/save")
async def api_users_save(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    uid = body.get("id")

    def _run():
        c = usr.connect()
        try:
            usr.init_db(c)
            if uid:
                if any(k in body for k in ("nama", "role", "aktif")):
                    r = usr.update_user(c, int(uid), nama=body.get("nama"), role=body.get("role"), aktif=body.get("aktif"))
                    if not r.get("ok"):
                        return r
                if body.get("password"):
                    r = usr.set_password(c, int(uid), body.get("password"))
                    if not r.get("ok"):
                        return r
                return {"ok": True}
            return usr.create_user(c, body.get("username", ""), body.get("password", ""), nama=body.get("nama", ""), role=body.get("role", "viewer"))
        finally:
            c.close()

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/users/delete")
async def api_users_delete(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    uid = body.get("id") if isinstance(body, dict) else None
    if not uid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        c = usr.connect()
        try:
            usr.init_db(c)
            return usr.delete_user(c, int(uid))
        finally:
            c.close()

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


try:
    _sc = usr.connect()
    _si = usr.seed_admin(_sc)
    _sc.close()
    if _si and _si.get("default_password"):
        print("[users] Admin awal: %s / %s  -- SEGERA GANTI via /users" % (_si["username"], _si["default_password"]))
except Exception as _e:
    print("[users] seed dilewati:", _e)

# =============================================================
# Konteks request (pengganti $_POST / $_GET / $_FILES)
# =============================================================
class Ctx:
    def __init__(self, run, form, files, query):
        self.run = run
        self.form = form      # dict[str,str]
        self.files = files    # dict[str, list[(bytes, filename)]]
        self.query = query    # dict[str,str]

    def P(self, name, default=""):
        v = self.form.get(name)
        return default if v is None else v

    def G(self, name, default=""):
        v = self.query.get(name)
        return default if v is None else v

    def R(self, name, default=""):
        if name in self.form:
            return self.form[name]
        if name in self.query:
            return self.query[name]
        return default

    def file(self, field):
        lst = self.files.get(field)
        if lst:
            return lst[0]  # (bytes, filename)
        return None

    def file_list(self, field):
        return self.files.get(field, [])


async def build_ctx(request: Request) -> Ctx:
    query = dict(request.query_params)
    form_fields = {}
    files = {}
    if request.method == "POST":
        form = await request.form()
        for key, value in form.multi_items():
            if isinstance(value, StarletteUploadFile):
                data = await value.read()
                files.setdefault(key, []).append((data, value.filename or ""))
            else:
                form_fields[key] = value  # last-wins, seperti $_POST
    run = query.get("run") or form_fields.get("run") or ""
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", run):
        raise Exception("Run ID tidak valid.")
    return Ctx(run, form_fields, files, query)


# =============================================================
# State & artifact (pengganti _runs/<run>/state.json)
# =============================================================
def run_dir(cfg, run, create=True):
    d = os.path.join(cfg["runs_dir"].rstrip("/"), run)
    if create and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def state_path(cfg, run):
    return os.path.join(run_dir(cfg, run), "state.json")


def load_state(cfg, run):
    p = state_path(cfg, run)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                if not isinstance(d.get("steps"), dict):
                    d["steps"] = {}
                return d
        except Exception:
            pass
    return {"run": run, "created": _dt.datetime.now().isoformat(), "steps": {}}


def save_state(cfg, run, state):
    with open(state_path(cfg, run), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def set_step(cfg, run, n, data):
    state = load_state(cfg, run)
    state["steps"][str(n)] = data
    save_state(cfg, run, state)
    return state


def get_state(cfg, run):
    state = load_state(cfg, run)
    return {
        "run": state.get("run", run),
        "ngrok_url": state.get("ngrok_url", ""),
        "steps": state.get("steps", {}),
    }


def reset_run(cfg, run):
    d = run_dir(cfg, run, create=False)
    if os.path.isdir(d):
        for name in os.listdir(d):
            try:
                os.remove(os.path.join(d, name))
            except Exception:
                pass
        try:
            os.rmdir(d)
        except Exception:
            pass
    return {"cleared": True}


def save_ngrok(cfg, run, url):
    state = load_state(cfg, run)
    state["ngrok_url"] = url
    save_state(cfg, run, state)


def mime_for_ext(ext):
    ext = (ext or "").lower()
    return {
        "json": "application/json; charset=utf-8",
        "xlsx": XLSX_MIME,
        "zip": "application/zip",
    }.get(ext, "application/octet-stream")


def save_artifact(cfg, run, n, ext, data_bytes, download_name, summary):
    if isinstance(data_bytes, str):
        data_bytes = data_bytes.encode("utf-8")
    fname = "step%s.%s" % (n, ext)
    with open(os.path.join(run_dir(cfg, run), fname), "wb") as f:
        f.write(data_bytes)
    data = {
        "status": "done",
        "file": fname,
        "name": download_name,
        "ext": ext,
        "mime": mime_for_ext(ext),
        "size": len(data_bytes),
        "summary": summary,
        "at": _dt.datetime.now().isoformat(),
    }
    set_step(cfg, run, n, data)
    return data


def resolve_input_bytes(cfg, ctx, upload_field, allowed_exts):
    up = ctx.file(upload_field)
    if up is not None:
        data, name = up
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        if allowed_exts and ext not in allowed_exts:
            raise Exception("Format file harus: %s. Diterima: %s" % (", ".join(allowed_exts), name))
        return data, name
    from_step = 0
    try:
        from_step = int(ctx.P("from_step", "0") or "0")
    except Exception:
        from_step = 0
    if from_step > 0:
        state = load_state(cfg, ctx.run)
        src = state["steps"].get(str(from_step))
        if not src or not src.get("file"):
            raise Exception("Hasil Step %d belum tersedia." % from_step)
        p = os.path.join(run_dir(cfg, ctx.run), src["file"])
        if not os.path.isfile(p):
            raise Exception("File hasil Step %d hilang dari server." % from_step)
        if allowed_exts and str(src.get("ext", "")).lower() not in allowed_exts:
            raise Exception("Hasil Step %d bukan format yang dibutuhkan (%s)." % (from_step, ", ".join(allowed_exts)))
        with open(p, "rb") as f:
            return f.read(), src.get("name", "")
    raise Exception("Tidak ada input. Unggah file atau pilih hasil step sebelumnya.")


def read_upload(ctx, field, exts, label):
    up = ctx.file(field)
    if up is None:
        raise Exception("File %s wajib diunggah." % label)
    data, name = up
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if exts and ext not in exts:
        raise Exception("File %s harus berformat %s." % (label, "/".join(exts)))
    return data, name


# =============================================================
# Google auth (service-account JWT -> access token)
# =============================================================
_token_cache = {"token": None, "exp": 0.0}


def google_token(cfg, ctx):
    override = (ctx.P("access_token", "") or "").strip()
    if override != "":
        return override
    now = time.time()
    if _token_cache["token"] and _token_cache["exp"] - 60 > now:
        return _token_cache["token"]
    file = cfg["service_account_file"]
    # Kalau PIPELINE_SA_FILE diisi tapi filenya tidak ada (mis. masih path
    # contoh), fallback otomatis ke service-account.json di folder web_app.py.
    if not os.path.isfile(file):
        fallback = os.path.join(BASE_DIR, "service-account.json")
        if os.path.isfile(fallback):
            file = fallback
        else:
            raise Exception(
                "service-account.json tidak ditemukan (dicek: '%s' dan '%s') dan "
                "Access Token kosong. Perbaiki PIPELINE_SA_FILE di .env, atau "
                "letakkan service-account.json di folder yang sama dengan web_app.py, "
                "atau tempel Access Token di form." % (cfg["service_account_file"], fallback)
            )
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GRequest
    except Exception:
        raise Exception("Library google-auth belum terpasang. Jalankan: pip install google-auth")
    try:
        creds = service_account.Credentials.from_service_account_file(
            file, scopes=[cfg["google_scope"]]
        )
        creds.refresh(GRequest())
    except Exception as e:
        raise Exception("Gagal meminta token Google: %s" % e)
    if not creds.token:
        raise Exception("Token Google gagal.")
    _token_cache["token"] = creds.token
    try:
        _token_cache["exp"] = creds.expiry.replace(tzinfo=_dt.timezone.utc).timestamp()
    except Exception:
        _token_cache["exp"] = now + 3000
    return creds.token


# =============================================================
# HTTP helper Google API
# =============================================================
def http_post_json(url, body, token, timeout=120):
    try:
        r = requests.post(
            url, json=body,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            timeout=timeout,
        )
    except Exception as e:
        return (0, None, str(e))
    try:
        j = r.json()
    except Exception:
        j = None
    return (r.status_code, j, r.text)


def http_get_json(url, query, token, timeout=120):
    try:
        r = requests.get(
            url, params=query,
            headers={"Authorization": "Bearer " + token},
            timeout=timeout,
        )
    except Exception as e:
        return (0, None, str(e))
    try:
        j = r.json()
    except Exception:
        j = None
    return (r.status_code, j, r.text)


# =============================================================
# Endpoint backend FastAPI (pengganti ngrok / localhost)
# =============================================================
def resolve_api_base(cfg, raw):
    if cfg["force_local_api"]:
        return cfg["local_api_base"].rstrip("/")
    raw = (raw or "").strip()
    if raw == "":
        return cfg["local_api_base"].rstrip("/")
    if not re.match(r"^https?://", raw, re.I):
        raise Exception("URL server harus diawali http:// atau https://")
    return raw.rstrip("/")


def api_endpoint(cfg, raw, suffix):
    base = resolve_api_base(cfg, raw)
    if base.endswith(suffix):
        return base
    return base + suffix


def _api_headers(cfg, extra=None):
    h = {"X-API-Key": cfg["qwen_api_key"], "ngrok-skip-browser-warning": "true"}
    if extra:
        h.update(extra)
    return h


def curl_multipart(cfg, endpoint, files, fields=None):
    """POST multipart, kembalikan (bytes, headers). Wajib balasan file (PK)."""
    multipart = {}
    for field, info in files.items():
        b, name = info[0], info[1]
        multipart[field] = (name, b, XLSX_MIME)
    r = requests.post(endpoint, headers=_api_headers(cfg), files=multipart, data=(fields or {}), timeout=3600)
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    if r.status_code < 200 or r.status_code >= 300:
        raise Exception("Server error (HTTP %d): %s" % (r.status_code, r.text[:300]))
    ctype = hdrs.get("content-type", "")
    if "json" in ctype.lower():
        raise Exception("Server membalas JSON, bukan file XLSX: %s" % r.text[:300])
    content = r.content
    if content[:2] != b"PK":
        peek = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content.decode("utf-8", "replace"))).strip()
        raise Exception("Server tidak mengembalikan file XLSX yang valid (mungkin halaman error/interstitial). Cuplikan: " + peek[:300])
    return content, hdrs


def curl_multipart_raw(cfg, endpoint, files, fields=None):
    """POST multipart, kembalikan bytes mentah (tak paksa PK/JSON)."""
    multipart = {}
    for field, info in files.items():
        b, name = info[0], info[1]
        mime = info[2] if len(info) > 2 else "application/octet-stream"
        multipart[field] = (name, b, mime)
    r = requests.post(endpoint, headers=_api_headers(cfg), files=multipart, data=(fields or {}), timeout=3600)
    if r.status_code < 200 or r.status_code >= 300:
        raise Exception("Server error (HTTP %d): %s" % (r.status_code, r.text[:1500]))
    return r.content


def curl_json_raw(cfg, endpoint, json_str):
    r = requests.post(
        endpoint, headers=_api_headers(cfg, {"Content-Type": "application/json"}),
        data=json_str.encode("utf-8"), timeout=3600,
    )
    if r.status_code < 200 or r.status_code >= 300:
        raise Exception("Server error (HTTP %d): %s" % (r.status_code, r.text[:1500]))
    return r.content


def curl_get_raw(cfg, endpoint):
    r = requests.get(endpoint, headers=_api_headers(cfg), timeout=60)
    if r.status_code < 200 or r.status_code >= 300:
        raise Exception("Server error (HTTP %d): %s" % (r.status_code, r.text[:1500]))
    return r.content


def curl_post_json(cfg, endpoint, files, fields=None):
    multipart = {}
    for field, info in files.items():
        b, name = info[0], info[1]
        mime = info[2] if len(info) > 2 else "application/octet-stream"
        multipart[field] = (name, b, mime)
    r = requests.post(endpoint, headers=_api_headers(cfg), files=multipart, data=(fields or {}), timeout=3600)
    if r.status_code < 200 or r.status_code >= 300:
        raise Exception("Server error (HTTP %d): %s" % (r.status_code, r.text[:300]))
    try:
        return r.json()
    except Exception:
        peek = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text)).strip()
        raise Exception("Server tidak mengembalikan JSON valid (mungkin halaman error/interstitial). Cuplikan: " + peek[:300])


# =============================================================
# XLSX helper (pakai openpyxl — fungsional identik dgn versi PHP)
# =============================================================
def xlsx_build(sheets):
    """sheets: list of {name, rows(aoa), widths?, wrapCols?(0-based idx)}."""
    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(title=str(sh["name"])[:31])
        wrap_cols = set(sh.get("wrapCols") or [])
        for r_i, row in enumerate(sh["rows"], start=1):
            for c_i, val in enumerate(row, start=1):
                cell_val = None if (val == "" or val is None) else val
                cell = ws.cell(row=r_i, column=c_i, value=cell_val)
                if r_i == 1:
                    cell.font = Font(bold=True)
                elif (c_i - 1) in wrap_cols:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        for i, w in enumerate(sh.get("widths") or [], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def zip_build(files):
    """files: list of {name, data(bytes)}."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            data = f["data"]
            if isinstance(data, str):
                data = data.encode("utf-8")
            z.writestr(f["name"], data)
    return bio.getvalue()


def _wb_from_bytes(b):
    return load_workbook(io.BytesIO(b), data_only=True)


def sheet_headers(ws):
    H = {}
    for c in range(1, (ws.max_column or 0) + 1):
        v = ws.cell(row=1, column=c).value
        if v is None:
            continue
        name = str(v).strip()
        if name != "":
            H[name] = c
    return H


def read_sheet(ws):
    """Kembalikan {headers:{name:col}, rows:{rownum:{col:value}}, maxRow}."""
    rows = {}
    maxrow = ws.max_row or 0
    maxcol = ws.max_column or 0
    for r in range(1, maxrow + 1):
        cells = {}
        for c in range(1, maxcol + 1):
            v = ws.cell(row=r, column=c).value
            if v is None:
                continue
            cells[c] = v
        if cells:
            rows[r] = cells
    headers = {}
    if 1 in rows:
        for c, v in rows[1].items():
            nm = str(v).strip()
            if nm != "":
                headers[nm] = c
    return {"headers": headers, "rows": rows, "maxRow": maxrow}


def _sv(cells, col):
    """String value dari sel (mirip perilaku PHP yang selalu string)."""
    if not col or col not in cells:
        return ""
    v = cells[col]
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _is_numeric(v):
    if v is None or v == "":
        return False
    try:
        float(str(v))
        return True
    except Exception:
        return False


def _find_header(headers, cands):
    for c in cands:
        for name, idx in headers.items():
            if str(name).strip().lower() == c.lower():
                return idx
    return None


def xlsx_upsert_sheet(src_bytes, sheet_name, aoa):
    """Buat/timpa 1 worksheet di workbook (mempertahankan sheet lain)."""
    wb = load_workbook(io.BytesIO(src_bytes))
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(title=str(sheet_name)[:31])
    for r_i, row in enumerate(aoa, start=1):
        for c_i, val in enumerate(row, start=1):
            cell_val = None if (val == "" or val is None) else val
            ws.cell(row=r_i, column=c_i, value=cell_val)
    ws.freeze_panes = "A2"
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# =============================================================
# STEP 1 — Tarik Log Dialogflow (Google Logging) -> JSON
# =============================================================
def _s2_match(text, regex, flags=0):
    m = re.search(regex, text or "", flags)
    if m:
        return m.group(1) if m.groups() else ""
    return ""


def step1_pull_logs(cfg, ctx):
    start = (ctx.P("start_date") or "").strip()
    end = (ctx.P("end_date") or "").strip()
    lang = (ctx.P("bahasa", "id") or "id").strip().lower()
    if lang not in ("id", "en"):
        raise Exception("Bahasa harus id atau en.")
    if not start:
        raise Exception("Start Date wajib diisi.")
    if not end:
        end = start
    start = start[:10]
    end = end[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start):
        raise Exception("Start Date harus YYYY-MM-DD.")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", end):
        raise Exception("End Date harus YYYY-MM-DD.")

    tz = _dt.timezone(_dt.timedelta(hours=7))
    try:
        from zoneinfo import ZoneInfo
        today = _dt.datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")
    except Exception:
        today = _dt.datetime.now(tz).strftime("%Y-%m-%d")
    if start > end:
        raise Exception("Start Date (%s) tidak boleh lebih besar dari End Date (%s)." % (start, end))
    if start >= today or end >= today:
        raise Exception("Pilih tanggal sebelum hari ini. Hari ini: %s." % today)

    day_ms = 86400000
    start_ms = int(_dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=tz).timestamp()) * 1000
    end_excl_ms = int(_dt.datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=tz).timestamp()) * 1000 + day_ms
    range_days = int((end_excl_ms - start_ms) / day_ms)
    if range_days > 31:
        raise Exception("Range terlalu besar: %d hari. Maksimal 31 hari." % range_days)

    # --- Cache-aware: pakai database bila sudah lengkap; kalau belum, tarik &
    #     simpan (ingest pintar per hari), lalu bangun ulang output dari DB.
    #     Jadi Step 1 & Dashboard berbagi satu database yang sama. ---
    conn = adb.init_db(adb.connect())
    try:
        need = adb.days_to_fetch(conn, start, end, lang, force=False)
    finally:
        conn.close()

    ing = None
    if need:
        ing = ingest.ensure_range(start, end, lang=lang, force=False, verbose=False)
        if ing and not ing.get("ok", True):
            raise Exception("Gagal menarik data dari Google: %s" % ing.get("note", ""))

    conn = adb.init_db(adb.connect())
    try:
        content, total_entries = adb.rebuild_entries_json(conn, start, end, lang)
        rng = adb.range_status(conn, start, end, lang)
    finally:
        conn.close()

    file_name = "Dialogflow_Log_%s_%s_to_%s.json" % (lang, start, end)
    summary = {
        "status": "Selesai",
        "total_entries": total_entries,
        "sumber": ("Dimuat dari database (data sudah lengkap, tanpa tarik ulang)"
                   if not need else
                   "Ditarik dari Google lalu disimpan ke database"),
        "hari_ditarik": (ing or {}).get("days_fetched", []),
        "hari_dilewati_lengkap": (len((ing or {}).get("days_skipped_complete", []))
                                  if ing else rng["complete"]),
        "kelengkapan": {"complete": rng["complete"], "partial": rng["partial"],
                        "missing": rng["missing"], "total_days": rng["total_days"]},
        "error_count": 0,
    }
    data = save_artifact(cfg, ctx.run, 1, "json", content, file_name, summary)
    return {"step": 1, "artifact": data}


# =============================================================
# STEP 2 — Convert JSON Log -> XLSX Multi Sheet
# =============================================================
def step2_json_to_xlsx(cfg, ctx):
    raw_bytes, orig_name = resolve_input_bytes(cfg, ctx, "json_file", ["json"])
    decoded = raw_bytes.decode("utf-8", "replace")
    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]
    decoded = decoded.strip()
    if decoded == "":
        raise Exception("File JSON kosong.")

    items = []
    parsed = None
    try:
        parsed = json.loads(decoded)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = [parsed]
    else:
        for line in re.split(r"\r?\n", decoded):
            line = line.strip()
            if line == "":
                continue
            try:
                v = json.loads(line)
            except Exception:
                continue
            if isinstance(v, list):
                items.extend(v)
            elif isinstance(v, dict):
                items.append(v)
        if not items:
            raise Exception("JSON tidak valid (gagal parse array/object maupun JSON Lines).")
    if not items:
        raise Exception("Tidak ada objek log di dalam file JSON.")

    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        insert_id = item.get("insertId", "")
        text_payload = item.get("textPayload", "")
        trace_id = user_phrase = bot_response = intent_name = lang = waktu = ""
        score = ""
        if text_payload:
            trace_id = _s2_match(text_payload, r'session_id:\s*"([^"]+)"')
            if not trace_id:
                trace_id = item.get("trace", "")
            waktu = _s2_match(text_payload, r'timestamp:\s*"([^"]+)"')
            user_phrase = _s2_match(text_payload, r'resolved_query:\s*"([^"]+)"')
            bot_response = _s2_match(text_payload, r'fulfillment\s*\{\s*speech:\s*"((?:[^"\\]|\\.)*?)"', re.S)
            bot_response = bot_response.replace("\\n", "\n")
            intent_name = _s2_match(text_payload, r'metadata\s*\{\s*[^}]+?intent_name:\s*"([^"]+)"', re.S)
            lang = _s2_match(text_payload, r'lang:\s*"([^"]+)"')
            score = _s2_match(text_payload, r'score:\s*([0-9.]+)')
        else:
            trace_id = item.get("trace", "")
        rows.append({
            "ID trace": trace_id, "waktu interaksi": waktu, "user phrase": user_phrase,
            "bot response": bot_response, "intent name": intent_name, "lang": lang,
            "insertId": insert_id, "score": ("" if score == "" else float(score)),
        })
    if not rows:
        raise Exception("Tidak ada baris yang dapat diproses dari file JSON.")

    counts = {}
    first_seen = 0
    for r in rows:
        it = r["intent name"]
        if it not in counts:
            counts[it] = {"count": 0, "seen": first_seen}
            first_seen += 1
        counts[it]["count"] += 1
    stats = [{"intent": k, "count": v["count"], "seen": v["seen"]} for k, v in counts.items()]
    stats.sort(key=lambda s: (-s["count"], s["seen"]))

    system_intents = {"System_System_Welcome Intent": 1, "System_System_Hubungi Agent": 1}
    fallback_intents = {"System_System_Fallback Intent": 1, "System_System_Fallback Intent 2": 1}
    system_rows, fallback_rows, one_char_rows, non_fallback_rows = [], [], [], []
    for r in rows:
        it = r["intent name"]
        phrase = str(r["user phrase"]).strip()
        if it in system_intents:
            system_rows.append(r)
        elif it in fallback_intents:
            fallback_rows.append(r)
        elif len(phrase) == 1:
            one_char_rows.append(r)
        else:
            non_fallback_rows.append(r)

    header = ["ID trace", "waktu interaksi", "user phrase", "bot response", "intent name", "lang", "insertId", "score"]

    def to_aoa(src):
        out = [header]
        for r in src:
            out.append([r["ID trace"], r["waktu interaksi"], r["user phrase"], r["bot response"], r["intent name"], r["lang"], r["insertId"], r["score"]])
        return out

    stat_aoa = [["intent name", "jumlah interaksi"]]
    for s in stats:
        stat_aoa.append([s["intent"], s["count"]])

    sheets = [
        {"name": "Interaksi", "rows": to_aoa(rows)},
        {"name": "Statistik Intent", "rows": stat_aoa},
        {"name": "System", "rows": to_aoa(system_rows)},
        {"name": "Fallback", "rows": to_aoa(fallback_rows)},
        {"name": "1 Karakter", "rows": to_aoa(one_char_rows)},
        {"name": "Non Fallback", "rows": to_aoa(non_fallback_rows)},
    ]
    xlsx = xlsx_build(sheets)
    base = re.sub(r"\.json$", "", orig_name, flags=re.I)
    if base == "":
        base = "output_combined_json_data"
    out_name = base + ".xlsx"
    summary = {
        "status": "Selesai", "source_file": orig_name, "total_rows": len(rows),
        "total_intents": len(stats), "total_system": len(system_rows),
        "total_fallback": len(fallback_rows), "total_one_character": len(one_char_rows),
        "total_non_fallback": len(non_fallback_rows),
    }
    data = save_artifact(cfg, ctx.run, 2, "xlsx", xlsx, out_name, summary)
    return {"step": 2, "artifact": data}


# =============================================================
# STEP 3 / 13 — Training Phrase & Intent -> 2 XLSX dalam ZIP
# =============================================================
def build_dialogflow_intent_zip(cfg, ctx):
    token = google_token(cfg, ctx)
    url = "https://dialogflow.googleapis.com/v2/projects/" + cfg["project_id"] + "/agent/intents"
    intents = []
    page_token = ""
    while True:
        q = {"intentView": "INTENT_VIEW_FULL", "pageSize": 1000, "languageCode": "id"}
        if page_token != "":
            q["pageToken"] = page_token
        status, jj, raw = http_get_json(url, q, token, 120)
        if status < 200 or status >= 300 or not isinstance(jj, dict) or "error" in jj:
            raise Exception("Gagal menarik intents Dialogflow: %s" % str(raw)[:300])
        if jj.get("intents"):
            intents.extend(jj["intents"])
        page_token = jj.get("nextPageToken", "") or ""
        if page_token == "":
            break
    if not intents:
        raise Exception("Tidak ada intent yang diterima dari Dialogflow API.")

    training_rows = []
    intent_rows = []
    catalog = []
    skipped_priority = skipped_child = no_training = no_response = 0
    for intent in intents:
        if int(intent.get("priority", 0) or 0) == -1:
            skipped_priority += 1
            continue
        if str(intent.get("parentFollowupIntentName", "") or "").strip() != "":
            skipped_child += 1
            continue
        display_name = str(intent.get("displayName", "") or "").strip()
        if display_name == "":
            continue
        phrases = []
        for tp in (intent.get("trainingPhrases") or []):
            txt = ""
            for part in (tp.get("parts") or []):
                txt += part.get("text", "") or ""
            txt = txt.strip()
            if txt != "":
                phrases.append(txt)
        if not phrases:
            no_training += 1
        for p in phrases:
            training_rows.append({"ID": display_name, "Training Phrase": p})
        responses = []
        for msg in (intent.get("messages") or []):
            t = msg.get("text", {})
            if isinstance(t, dict) and isinstance(t.get("text"), list):
                for x in t["text"]:
                    x = str(x).strip()
                    if x != "":
                        responses.append(x)
            sp = msg.get("speech")
            if isinstance(sp, str) and sp.strip() != "":
                responses.append(sp.strip())
        seen = set()
        uniq = []
        for x in responses:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        response_text = "\n\n".join(uniq)
        if response_text == "":
            no_response += 1
        intent_rows.append({"ID": display_name, "Isi Intent": response_text})
        catalog.append({"intent": display_name, "lang": "id", "training_phrases": phrases, "answer": response_text})

    if not training_rows:
        raise Exception("Tidak ada training phrase yang berhasil diekstrak.")
    if not intent_rows:
        raise Exception("Tidak ada intent yang berhasil diekstrak.")

    training_rows.sort(key=lambda r: (r["ID"], r["Training Phrase"]))
    intent_rows.sort(key=lambda r: r["ID"])

    train_aoa = [["ID", "Training Phrase"]] + [[r["ID"], r["Training Phrase"]] for r in training_rows]
    intent_aoa = [["ID", "Isi Intent"]] + [[r["ID"], r["Isi Intent"]] for r in intent_rows]

    train_xlsx = xlsx_build([{"name": "Sheet1", "rows": train_aoa, "widths": [55, 70], "wrapCols": [1]}])
    intent_xlsx = xlsx_build([{"name": "Sheet1", "rows": intent_aoa, "widths": [55, 100], "wrapCols": [1]}])

    zip_bytes = zip_build([
        {"name": "Analisis Fallback - Training Phrase.xlsx", "data": train_xlsx},
        {"name": "Analisis Fallback - Intent.xlsx", "data": intent_xlsx},
    ])
    summary = {
        "status": "Selesai",
        "total_intent_dari_api": len(intents),
        "total_intent_diekspor": len(intent_rows),
        "total_training_phrase": len(training_rows),
        "intent_priority_minus_1_dilewati": skipped_priority,
        "intent_anakan_dilewati": skipped_child,
        "intent_tanpa_training_phrase": no_training,
        "intent_tanpa_respons_teks": no_response,
    }
    # Katalog dwibahasa: augmentasi entri bahasa Inggris (best-effort).
    try:
        _en_intents = []
        _pt = ""
        while True:
            _q = {"intentView": "INTENT_VIEW_FULL", "pageSize": 1000, "languageCode": "en"}
            if _pt != "":
                _q["pageToken"] = _pt
            _st, _jj, _raw = http_get_json(url, _q, token, 120)
            if _st < 200 or _st >= 300 or not isinstance(_jj, dict) or "error" in _jj:
                break
            if _jj.get("intents"):
                _en_intents.extend(_jj["intents"])
            _pt = _jj.get("nextPageToken", "") or ""
            if _pt == "":
                break
        for intent in _en_intents:
            if int(intent.get("priority", 0) or 0) == -1:
                continue
            if str(intent.get("parentFollowupIntentName", "") or "").strip() != "":
                continue
            _dn = str(intent.get("displayName", "") or "").strip()
            if _dn == "":
                continue
            _ph = []
            for tp in (intent.get("trainingPhrases") or []):
                _txt = ""
                for part in (tp.get("parts") or []):
                    _txt += part.get("text", "") or ""
                _txt = _txt.strip()
                if _txt != "":
                    _ph.append(_txt)
            _resp = []
            for msg in (intent.get("messages") or []):
                _t = msg.get("text", {})
                if isinstance(_t, dict) and isinstance(_t.get("text"), list):
                    for x in _t["text"]:
                        x = str(x).strip()
                        if x != "":
                            _resp.append(x)
                _sp = msg.get("speech")
                if isinstance(_sp, str) and _sp.strip() != "":
                    _resp.append(_sp.strip())
            _seen = set()
            _uniq = []
            for x in _resp:
                if x not in _seen:
                    _seen.add(x)
                    _uniq.append(x)
            _rt = "\n\n".join(_uniq)
            if not _ph and _rt == "":
                continue
            catalog.append({"intent": _dn, "lang": "en", "training_phrases": _ph, "answer": _rt})
    except Exception:
        pass
    return zip_bytes, summary, catalog


def _intent_freq_map(conn=None):
    """Peta {nama_intent: jumlah_panggil} dari analitik (utk prioritas deskripsi)."""
    own = conn is None
    if own:
        try:
            conn = adb.connect()
        except Exception:
            return {}
    try:
        try:
            rows = adb.top_intents(conn, limit=100000, include_system=True, include_umum=True)
        except Exception:
            rows = []
        m = {}
        for r in (rows or []):
            nm = r.get("intent") if isinstance(r, dict) else None
            if nm:
                m[nm] = int(r.get("count") or 0)
        return m
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


def _sync_catalog_safe(catalog, summary=None):
    """Selaraskan Katalog Intent dari hasil tarik Dialogflow. Tidak crash step."""
    try:
        conn = imdb.init_db(imdb.connect())
        try:
            freq = {}
            try:
                freq = _intent_freq_map(conn)
            except Exception:
                freq = {}
            stats = imdb.sync_catalog(conn, catalog or [], freq_map=freq)
            if isinstance(summary, dict):
                summary["katalog_intent"] = stats
            return stats
        finally:
            conn.close()
    except Exception as e:
        if isinstance(summary, dict):
            summary["katalog_intent_error"] = str(e)
        return None


def step3_training_intent(cfg, ctx):
    zip_bytes, summary, catalog = build_dialogflow_intent_zip(cfg, ctx)
    _sync_catalog_safe(catalog, summary)
    data = save_artifact(cfg, ctx.run, 3, "zip", zip_bytes, "Analisis Fallback - Database Dialogflow.zip", summary)
    return {"step": 3, "artifact": data}


def avaya2_pull_intents(cfg, ctx):
    zip_bytes, summary, catalog = build_dialogflow_intent_zip(cfg, ctx)
    _sync_catalog_safe(catalog, summary)
    data = save_artifact(cfg, ctx.run, 13, "zip", zip_bytes, "Avaya - Database Intent Dialogflow.zip", summary)
    return {"step": 13, "artifact": data}


def extract_training_intent(zip_bytes):
    train = content = None
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if os.path.splitext(name)[1].lower() != ".xlsx":
                continue
            if train is None and "training" in name.lower():
                train = z.read(name)
                continue
            if content is None and "intent" in name.lower():
                content = z.read(name)
    if train is None:
        raise Exception("File 'Training Phrase' tidak ada di ZIP Step 3.")
    if content is None:
        raise Exception("File 'Intent' tidak ada di ZIP Step 3.")
    return train, content


# =============================================================
# STEP 12 — Avaya upload JSON (gabung + dedup sid)
# =============================================================
def avaya1_upload_json(cfg, ctx):
    uploads = ctx.file_list("json_files[]")
    if not uploads:
        raise Exception("Unggah minimal satu file JSON AWE Avaya.")
    all_rows = []
    per_file = []
    for data, name in uploads:
        try:
            parsed = json.loads(data.decode("utf-8", "replace"))
        except Exception:
            raise Exception("JSON tidak valid: " + name)
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            parsed = parsed["data"]
        if not isinstance(parsed, (list, dict)):
            raise Exception("Struktur JSON tak dikenal: " + name)
        if isinstance(parsed, dict):
            parsed = [parsed]
        cnt = 0
        for row in parsed:
            if isinstance(row, dict):
                all_rows.append(row)
                cnt += 1
        per_file.append("%s (%d)" % (name, cnt))
    if not all_rows:
        raise Exception("Tidak ada percakapan pada file JSON.")
    seen = set()
    merged = []
    for row in all_rows:
        sid = str(row.get("sid", "")) if row.get("sid") is not None else ""
        if sid != "":
            if sid in seen:
                continue
            seen.add(sid)
        merged.append(row)
    bytes_out = json.dumps(merged, ensure_ascii=False)
    dates = sorted([str(r.get("tanggal", ""))[:10] for r in merged if r.get("tanggal")])
    summary = {
        "status": "Selesai",
        "file_diunggah": len(per_file),
        "rincian_file": per_file,
        "total_percakapan_gabungan": len(merged),
        "duplikat_sid_dibuang": len(all_rows) - len(merged),
        "rentang_tanggal": (dates[0] + " s/d " + dates[-1]) if dates else "-",
    }
    data = save_artifact(cfg, ctx.run, 12, "json", bytes_out, "avaya_gabungan.json", summary)
    return {"step": 12, "artifact": data}


# =============================================================
# STEP 14/15/16 + diag — proxy ke backend Avaya
# =============================================================
def _avaya_summary_from_result(result, src_label=None):
    d = result.get("dashboard", {}) or {}
    meta = d.get("meta", {}) or {}
    cov = d.get("intent_coverage", {}) or {}
    defl = d.get("deflection", {}) or {}
    summary = {
        "status": "Selesai",
        "build_server": result.get("build", "-"),
        "mesin": meta.get("engine", "-"),
        "total_percakapan": meta.get("total_conv", "-"),
        "rentang_tanggal": "%s s/d %s" % (meta.get("date_min", "?"), meta.get("date_max", "?")),
        "intent_tercover": cov.get("covered", "-"),
        "intent_belum_tercover": cov.get("uncovered", "-"),
        "deflection_gap": defl.get("gap", "-"),
    }
    if src_label is not None:
        summary["sumber_intent"] = src_label
    return summary


def _avaya_persist(result, n_files=0, source="upload"):
    """Simpan hasil analisis AWE ke database avaya.db (biar tak perlu analisa ulang)."""
    try:
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.save_run(conn, result.get("dashboard", {}) or {},
                                 records=result.get("records", []) or [],
                                 n_files=n_files, source=source,
                                 build=result.get("build"))
        finally:
            conn.close()
    except Exception as _e:
        print("[AWE persist] gagal simpan: %r" % _e, flush=True)
        return None


def _avaya_inputs(cfg, ctx):
    state = load_state(cfg, ctx.run)
    s12 = state["steps"].get("12")
    if not s12 or not s12.get("file"):
        raise Exception("Jalankan Step 12 (upload JSON) dulu.")
    with open(os.path.join(run_dir(cfg, ctx.run), s12["file"]), "rb") as f:
        json_bytes = f.read()
    mode = ctx.P("mode", "auto")
    src_label = "Unggah manual"
    if mode == "manual":
        train, _ = read_upload(ctx, "training_file", ["xlsx"], "Training Phrase")
        content, _ = read_upload(ctx, "content_file", ["xlsx"], "Intent")
    else:
        if state["steps"].get("13", {}).get("file"):
            src_step = 13
        elif state["steps"].get("3", {}).get("file"):
            src_step = 3
        else:
            raise Exception("Jalankan Step 13 (tarik intent) dulu, atau pilih Unggah manual.")
        with open(os.path.join(run_dir(cfg, ctx.run), state["steps"][str(src_step)]["file"]), "rb") as f:
            zip_bytes = f.read()
        train, content = extract_training_intent(zip_bytes)
        src_label = "Step %d" % src_step
    files = {
        "files": (json_bytes, "avaya_gabungan.json", "application/json"),
        "file_training": (train, "training.xlsx", XLSX_MIME),
        "file_intent": (content, "intent.xlsx", XLSX_MIME),
    }
    return files, src_label


def avaya3_analyze(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-result")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    files, src_label = _avaya_inputs(cfg, ctx)
    body = curl_multipart_raw(cfg, endpoint, files)
    try:
        result = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        result = None
    if not isinstance(result, dict) or "dashboard" not in result:
        raise Exception("Server tidak mengembalikan JSON hasil yang valid. Cuplikan: " + body.decode("utf-8", "replace")[:800])
    summary = _avaya_summary_from_result(result, src_label)
    _info = _avaya_persist(result, source="upload")
    if _info:
        summary["disimpan_ke_database"] = "Ya (id %s)" % _info["id"]
    data = save_artifact(cfg, ctx.run, 14, "json", json.dumps(result, ensure_ascii=False), "avaya_result.json", summary)
    return {"step": 14, "artifact": data}


def avaya3_start(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-result-start")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    files, _ = _avaya_inputs(cfg, ctx)
    body = curl_multipart_raw(cfg, endpoint, files)
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        j = None
    if not isinstance(j, dict) or not j.get("job_id"):
        raise Exception("Server tidak memberi job_id. Cuplikan: " + body.decode("utf-8", "replace")[:800])
    return {"job_id": j["job_id"], "build": j.get("build", "-")}


def avaya3_progress(cfg, ctx):
    job = (ctx.G("job") or "").strip()
    if job == "":
        raise Exception("job_id kosong.")
    raw_base = (ctx.G("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-progress") + "?job=" + requests.utils.quote(job)
    body = curl_get_raw(cfg, endpoint)
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        j = None
    if not isinstance(j, dict):
        raise Exception("Progress gagal di-parse. Cuplikan: " + body.decode("utf-8", "replace")[:400])
    return {"progress": j}


def avaya3_fetch(cfg, ctx):
    job = (ctx.G("job") or "").strip()
    if job == "":
        raise Exception("job_id kosong.")
    raw_base = (ctx.G("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-result-fetch") + "?job=" + requests.utils.quote(job)
    body = curl_get_raw(cfg, endpoint)
    try:
        result = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        result = None
    if not isinstance(result, dict):
        raise Exception("Hasil gagal di-parse. Cuplikan: " + body.decode("utf-8", "replace")[:800])
    if result.get("finished") is False and "dashboard" not in result:
        return {"pending": True, "progress": result}
    if "dashboard" not in result:
        raise Exception("Hasil tidak berisi dashboard. Cuplikan: " + body.decode("utf-8", "replace")[:800])
    summary = _avaya_summary_from_result(result)
    _info = _avaya_persist(result, source="upload")
    if _info:
        summary["disimpan_ke_database"] = "Ya (id %s)" % _info["id"]
    data = save_artifact(cfg, ctx.run, 14, "json", json.dumps(result, ensure_ascii=False), "avaya_result.json", summary)
    return {"step": 14, "artifact": data}


def avaya4_dashboard(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-render")
    state = load_state(cfg, ctx.run)
    s14 = state["steps"].get("14")
    if not s14 or not s14.get("file"):
        raise Exception("Jalankan Step 14 (analisis) dulu.")
    with open(os.path.join(run_dir(cfg, ctx.run), s14["file"]), "rb") as f:
        result = json.loads(f.read().decode("utf-8", "replace"))
    if "dashboard" not in result:
        raise Exception("Hasil Step 14 tidak berisi data dashboard.")
    payload = json.dumps({"dashboard": result["dashboard"]}, ensure_ascii=False)
    html = curl_json_raw(cfg, endpoint, payload)
    html_text = html.decode("utf-8", "replace")
    if "<" not in html_text:
        raise Exception("Server tidak mengembalikan HTML. Cuplikan: " + html_text[:800])
    with open(os.path.join(run_dir(cfg, ctx.run), "step15_dashboard.html"), "wb") as f:
        f.write(html)
    summary = {"status": "Selesai", "ukuran_html": "%d bytes" % len(html), "catatan": 'Klik "Buka Dashboard" untuk melihat.'}
    data = save_artifact(cfg, ctx.run, 15, "html", html, "dashboard.html", summary)
    return {"step": 15, "artifact": data}


def avaya5_excel(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-excel")
    state = load_state(cfg, ctx.run)
    s14 = state["steps"].get("14")
    if not s14 or not s14.get("file"):
        raise Exception("Jalankan Step 14 (analisis) dulu.")
    with open(os.path.join(run_dir(cfg, ctx.run), s14["file"]), "rb") as f:
        result = json.loads(f.read().decode("utf-8", "replace"))
    if "dashboard" not in result:
        raise Exception("Hasil Step 14 tidak berisi data dashboard.")
    payload = json.dumps({"dashboard": result["dashboard"], "records": result.get("records", [])}, ensure_ascii=False)
    xlsx = curl_json_raw(cfg, endpoint, payload)
    if xlsx[:2] != b"PK":
        peek = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xlsx.decode("utf-8", "replace"))).strip()
        raise Exception("Server tidak mengembalikan file XLSX valid. Cuplikan: " + peek[:800])
    summary = {"status": "Selesai", "ukuran": "%d bytes" % len(xlsx), "isi_sheet": "Ringkasan, Percakapan, Agent, Pelanggan NPWP/Non-NPWP, Kandidat Intent Baru"}
    data = save_artifact(cfg, ctx.run, 16, "xlsx", xlsx, "hasil_avaya.xlsx", summary)
    return {"step": 16, "artifact": data}


def avaya_diag(cfg, ctx):
    raw_base = (ctx.G("ngrok_url") or ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/avaya-diag")
    body = curl_get_raw(cfg, endpoint)
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        j = None
    if not isinstance(j, dict):
        raise Exception("Diagnostik gagal di-parse. Cuplikan: " + body.decode("utf-8", "replace")[:800])
    diag = j.get("diag", j)
    diag["endpoint"] = endpoint
    return {"diag": diag}


# =============================================================
# STEP 4 / 5 / 7 — proxy analisis ke backend
# =============================================================
def step4_analyze(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/analyze-fallback")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    mode = ctx.P("mode", "auto")
    if mode == "manual":
        main_bytes, main_name = read_upload(ctx, "main_file", ["xlsx"], "Workbook utama")
        train_bytes, _ = read_upload(ctx, "training_file", ["xlsx"], "Training Phrase")
        content_bytes, _ = read_upload(ctx, "content_file", ["xlsx"], "Intent")
    else:
        state = load_state(cfg, ctx.run)
        s2 = state["steps"].get("2")
        if not s2 or not s2.get("file"):
            raise Exception("Hasil Step 2 belum ada. Jalankan Step 2 dulu.")
        main_path = os.path.join(run_dir(cfg, ctx.run), s2["file"])
        if not os.path.isfile(main_path):
            raise Exception("File hasil Step 2 hilang dari server.")
        with open(main_path, "rb") as f:
            main_bytes = f.read()
        main_name = s2["name"]
        s3 = state["steps"].get("3")
        if not s3 or not s3.get("file"):
            raise Exception("Hasil Step 3 belum ada. Jalankan Step 3 dulu.")
        zip_path = os.path.join(run_dir(cfg, ctx.run), s3["file"])
        if not os.path.isfile(zip_path):
            raise Exception("File hasil Step 3 hilang dari server.")
        with open(zip_path, "rb") as f:
            train_bytes, content_bytes = extract_training_intent(f.read())
    result, _ = curl_multipart(cfg, endpoint, {
        "file": (main_bytes, main_name or "main.xlsx"),
        "file_training": (train_bytes, "Analisis Fallback - Training Phrase.xlsx"),
        "file_content": (content_bytes, "Analisis Fallback - Intent.xlsx"),
    })
    summary = {
        "status": "Selesai", "endpoint": endpoint,
        "sumber": "Unggah 3 file" if mode == "manual" else "Otomatis (Step 2 + Step 3)",
        "output_size": len(result),
    }
    data = save_artifact(cfg, ctx.run, 4, "xlsx", result, "hasil_top5_hybrid.xlsx", summary)
    return {"step": 4, "artifact": data}


def step5_qwen_judge(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    if raw_base == "":
        state = load_state(cfg, ctx.run)
        raw_base = state.get("ngrok_url", "")
    endpoint = api_endpoint(cfg, raw_base, "/api/judge-xlsx")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    b, orig_name = resolve_input_bytes(cfg, ctx, "xlsx_file", ["xlsx"])
    result, _ = curl_multipart(cfg, endpoint, {"file": (b, orig_name or "hasil_top5_hybrid.xlsx")})
    base2 = re.sub(r"\.xlsx$", "", orig_name or "hasil_top5_hybrid", flags=re.I)
    summary = {"status": "Selesai", "endpoint": endpoint, "source_file": orig_name, "output_size": len(result)}
    data = save_artifact(cfg, ctx.run, 5, "xlsx", result, base2 + "_judged.xlsx", summary)
    return {"step": 5, "artifact": data}


def step7_mkta(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/mkta-analyze")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    mode = ctx.P("mode", "auto")
    state = load_state(cfg, ctx.run)
    if mode == "manual":
        b, name = read_upload(ctx, "xlsx_file", ["xlsx"], 'Workbook (punya sheet Non Fallback)')
    else:
        pick = None
        for s in ["6", "5", "4", "2"]:
            st = state["steps"].get(s)
            if st and st.get("file") and str(st.get("ext", "")).lower() == "xlsx":
                pick = st
                break
        if not pick:
            raise Exception('Belum ada hasil ber-sheet "Non Fallback". Jalankan minimal Step 2 (idealnya sampai Step 6).')
        p = os.path.join(run_dir(cfg, ctx.run), pick["file"])
        if not os.path.isfile(p):
            raise Exception("File sumber hilang dari server.")
        with open(p, "rb") as f:
            b = f.read()
        name = pick["name"]
    files = {"file": (b, name or "input.xlsx")}
    try:
        s3 = state["steps"].get("3")
        if s3 and s3.get("file"):
            zp = os.path.join(run_dir(cfg, ctx.run), s3["file"])
            if os.path.isfile(zp):
                with open(zp, "rb") as f:
                    tb, cb = extract_training_intent(f.read())
                files["file_training"] = (tb, "training_phrase.xlsx")
                files["file_content"] = (cb, "intent_content.xlsx")
    except Exception:
        pass
    result, _ = curl_multipart(cfg, endpoint, files)
    summary = {"status": "Selesai", "endpoint": endpoint, "output_size": len(result)}
    data = save_artifact(cfg, ctx.run, 7, "xlsx", result, "hasil_analisis_mkta.xlsx", summary)
    return {"step": 7, "artifact": data}


# =============================================================
# STEP 6 — Cross-check manual (Analisis Fallback)
# =============================================================
def step6_source_bytes(cfg, ctx):
    up = ctx.file("xlsx_file")
    if up is not None:
        return up[0]
    state = load_state(cfg, ctx.run)
    s6 = state["steps"].get("6")
    if s6 and s6.get("file"):
        p = os.path.join(run_dir(cfg, ctx.run), s6["file"])
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    s5 = state["steps"].get("5")
    if not s5 or not s5.get("file"):
        raise Exception("Hasil Step 5 belum ada. Jalankan Step 5 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s5["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 5 hilang dari server.")
    with open(p, "rb") as f:
        return f.read()


def step6_load(cfg, ctx):
    b = step6_source_bytes(cfg, ctx)
    with open(os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx"), "wb") as f:
        f.write(b)
    wb = _wb_from_bytes(b)
    if "Rekomendasi Fallback" not in wb.sheetnames:
        raise Exception('Sheet "Rekomendasi Fallback" tidak ada di file.')
    if "Analisis Fallback" not in wb.sheetnames:
        raise Exception('Sheet "Analisis Fallback" tidak ada. Pastikan memakai hasil Step 5.')
    llm = read_sheet(wb["Rekomendasi Fallback"])
    analisis = read_sheet(wb["Analisis Fallback"])
    H = llm["headers"]
    col_q = H.get("Pertanyaan User")
    col_ins = H.get("InserId") or H.get("InsertId")
    rek = {}
    for r in range(1, 6):
        rek[r] = {
            "id": H.get("Rek_%d_ID" % r),
            "ans": H.get("Rek_%d_Jawaban" % r),
            "skor": H.get("Rek_%d_Skor_Deteksi" % r),
            "conf": H.get("Rek_%d_Confidence" % r),
        }
    llm_by_ins = {}
    llm_by_order = []
    for rn in sorted(llm["rows"].keys()):
        if rn == 1:
            continue
        cells = llm["rows"][rn]
        opts = []
        for r in range(1, 6):
            _id = _sv(cells, rek[r]["id"]).strip()
            if _id == "":
                continue
            opts.append({"id": _id, "ans": _sv(cells, rek[r]["ans"]), "skor": _sv(cells, rek[r]["skor"]), "conf": _sv(cells, rek[r]["conf"])})
        if not opts and _sv(cells, col_q).strip() == "":
            continue
        obj = {"pertanyaan": _sv(cells, col_q), "options": opts}
        ins = _sv(cells, col_ins).strip()
        if ins != "":
            llm_by_ins[ins] = obj
        llm_by_order.append(obj)

    AH = analisis["headers"]
    a_q = AH.get("Pertanyaan User")
    a_cat = AH.get("Catatan LLM")
    a_intent = AH.get("Intent Judgement LLM")
    a_ins = AH.get("InsertId") or AH.get("InserId")
    out = []
    order = 0
    for rn in sorted(analisis["rows"].keys()):
        if rn == 1:
            continue
        cells = analisis["rows"][rn]
        ins = _sv(cells, a_ins).strip()
        pert = _sv(cells, a_q)
        cat = _sv(cells, a_cat)
        intent = _sv(cells, a_intent)
        if pert == "" and intent == "" and ins == "":
            continue
        match = llm_by_ins.get(ins) if (ins != "" and ins in llm_by_ins) else (llm_by_order[order] if order < len(llm_by_order) else None)
        opts = match["options"] if match else []
        skor = conf = ""
        for o in opts:
            if o["id"] == intent:
                skor = o["skor"]
                conf = o["conf"]
                break
        out.append({"row": rn, "pertanyaan": pert, "catatan": cat, "intent": intent, "skor": skor, "conf": conf, "options": opts})
        order += 1
    return {"step": 6, "rows": out, "total": len(out)}


def step6_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw)
    except Exception:
        edits = None
    if not isinstance(edits, list):
        raise Exception("Data edit tidak valid.")
    src_path = os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx")
    if not os.path.isfile(src_path):
        with open(src_path, "wb") as f:
            f.write(step6_source_bytes(cfg, ctx))
    with open(src_path, "rb") as f:
        src_bytes = f.read()
    wb = load_workbook(io.BytesIO(src_bytes))
    if "Analisis Fallback" not in wb.sheetnames:
        raise Exception('Sheet "Analisis Fallback" tidak ada.')
    ws = wb["Analisis Fallback"]
    H = sheet_headers(ws)
    col_intent = H.get("Intent Judgement LLM")
    col_isi = H.get("Isi Intent")
    if not col_intent:
        raise Exception('Kolom "Intent Judgement LLM" tidak ada di sheet.')
    changed = 0
    for e in edits:
        try:
            rn = int(e.get("row"))
        except Exception:
            continue
        if rn < 2:
            continue
        intent = e.get("intent", "")
        ws.cell(row=rn, column=col_intent, value=(intent if intent != "" else None))
        if col_isi and "isi" in e:
            isi = e.get("isi", "")
            ws.cell(row=rn, column=col_isi, value=(isi if isi != "" else None))
        changed += 1
    bio = io.BytesIO()
    wb.save(bio)
    out_bytes = bio.getvalue()
    summary = {"status": "Selesai", "baris_diperbarui": changed, "catatan": "Koreksi manual disimpan ke sheet Analisis Fallback."}
    data = save_artifact(cfg, ctx.run, 6, "xlsx", out_bytes, "hasil_crosscheck_manual.xlsx", summary)
    # perbarui juga step6_source agar konsisten utk load berikutnya
    with open(os.path.join(run_dir(cfg, ctx.run), "step6_source.xlsx"), "wb") as f:
        f.write(out_bytes)
    return {"step": 6, "artifact": data}


# =============================================================
# STEP 8 — Putusan LLM MKTA (QA Conf < threshold -> Qwen)
# =============================================================
def step8_base_bytes(cfg, ctx, prefer_step8=True):
    state = load_state(cfg, ctx.run)
    if prefer_step8:
        s8 = state["steps"].get("8")
        if s8 and s8.get("file"):
            p = os.path.join(run_dir(cfg, ctx.run), s8["file"])
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
    src_path = os.path.join(run_dir(cfg, ctx.run), "step8_source.xlsx")
    if os.path.isfile(src_path):
        with open(src_path, "rb") as f:
            return f.read()
    s7 = state["steps"].get("7")
    if not s7 or not s7.get("file"):
        raise Exception("Hasil Step 7 belum ada. Jalankan Step 7 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s7["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 7 hilang dari server.")
    with open(p, "rb") as f:
        return f.read()


def verdict_stats(xlsx_bytes, threshold):
    wb = _wb_from_bytes(xlsx_bytes)
    if "QA Conf MKTA" not in wb.sheetnames:
        return {"filled": 0, "remaining": 0, "total_below": 0}
    sh = read_sheet(wb["QA Conf MKTA"])
    H = sh["headers"]
    col_score = _find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = _find_header(H, ["PUTUSAN"])
    filled = remaining = total_below = 0
    for rn in sorted(sh["rows"].keys()):
        if rn == 1:
            continue
        cells = sh["rows"][rn]
        sc = _sv(cells, col_score)
        put = _sv(cells, col_put).strip()
        if col_put and put != "":
            filled += 1
        if _is_numeric(sc) and float(sc) < threshold:
            total_below += 1
            if put == "":
                remaining += 1
    return {"filled": filled, "remaining": remaining, "total_below": total_below}


def step8_load(cfg, ctx):
    up = ctx.file("xlsx_file")
    if up is not None:
        b = up[0]
        with open(os.path.join(run_dir(cfg, ctx.run), "step8_source.xlsx"), "wb") as f:
            f.write(b)
    else:
        b = step8_base_bytes(cfg, ctx, prefer_step8=False)
    wb = _wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada. Pastikan memakai hasil Step 7.')
    sh = read_sheet(wb["QA Conf MKTA"])
    H = sh["headers"]
    col_score = _find_header(H, ["Skor Pemrosesan Bahasa"])
    if not col_score:
        raise Exception('Kolom "Skor Pemrosesan Bahasa" tidak ada.')
    scores = []
    for rn in sorted(sh["rows"].keys()):
        if rn == 1:
            continue
        sc = _sv(sh["rows"][rn], col_score)
        if _is_numeric(sc):
            scores.append(float(sc))
    counts = []
    th = 0.4
    while th <= 0.8 + 1e-9:
        c = sum(1 for s in scores if s < th)
        counts.append({"th": round(th, 2), "count": c})
        th += 0.05
    return {"step": 8, "mode": "auto", "total": len(scores), "counts": counts}


def step8_run(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/mkta-verdict")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    if threshold < 0.4:
        threshold = 0.4
    if threshold > 0.8:
        threshold = 0.8
    limit = cfg["mkta_chunk"]
    base_bytes = step8_base_bytes(cfg, ctx, prefer_step8=True)
    prev = verdict_stats(base_bytes, threshold)
    result, hdrs = curl_multipart(cfg, endpoint, {"file": (base_bytes, "mkta.xlsx")},
                                  {"threshold": str(threshold), "limit": str(limit)})
    after = verdict_stats(result, threshold)
    processed = after["filled"] - prev["filled"]
    if processed < 0:
        processed = 0
    if processed == 0 and hdrs.get("x-processed") is not None:
        try:
            processed = int(hdrs.get("x-processed"))
        except Exception:
            pass
    remaining = after["remaining"]
    done = remaining <= 0
    summary = {
        "status": "Selesai" if done else "Sebagian (lanjutkan lagi)",
        "threshold": threshold, "diproses_batch_ini": processed,
        "sisa_belum_diputus": remaining, "total_di_bawah_threshold": after["total_below"],
        "chunk": limit,
    }
    data = save_artifact(cfg, ctx.run, 8, "xlsx", result, "hasil_putusan_mkta.xlsx", summary)
    return {"step": 8, "artifact": data, "processed": processed, "remaining": remaining, "done": done}


# =============================================================
# STEP 9 — Analisis Manual MKTA (Isi Intent Seharusnya)
# =============================================================
def step9_base_bytes(cfg, ctx):
    state = load_state(cfg, ctx.run)
    s9 = state["steps"].get("9")
    if s9 and s9.get("file"):
        p = os.path.join(run_dir(cfg, ctx.run), s9["file"])
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    s8 = state["steps"].get("8")
    if not s8 or not s8.get("file"):
        raise Exception("Hasil Step 8 belum ada. Jalankan Step 8 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s8["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 8 hilang dari server.")
    with open(p, "rb") as f:
        return f.read()


def step9_load(cfg, ctx):
    up = ctx.file("xlsx_file")
    if up is not None:
        b = up[0]
        with open(os.path.join(run_dir(cfg, ctx.run), "step9_source.xlsx"), "wb") as f:
            f.write(b)
    else:
        b = step9_base_bytes(cfg, ctx)
    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    wb = _wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = _find_header(H, ["ID trace", "ID Trace", "IDtrace"])
    col_user = _find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = _find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = _find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = _find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = _find_header(H, ["PUTUSAN"])
    col_alasan = _find_header(H, ["ALASAN", "Alasan"])
    prior = {}
    if "Analisis MKTA" in wb.sheetnames:
        am = read_sheet(wb["Analisis MKTA"])
        AH = am["headers"]
        p_id = _find_header(AH, ["ID trace", "ID Trace"])
        p_user = _find_header(AH, ["user phrase", "User Phrase", "Pertanyaan User"])
        p_llm = _find_header(AH, ["Intent Seharusnya (LLM)", "INTENT SEHARUSNYA"])
        p_man = _find_header(AH, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
        p_cat = _find_header(AH, ["Catatan", "CATATAN"])
        for rn in sorted(am["rows"].keys()):
            if rn == 1:
                continue
            cells = am["rows"][rn]
            key = _sv(cells, p_id) + "||" + _sv(cells, p_user)
            prior[key] = {
                "llm": _sv(cells, p_llm),
                "manual": _sv(cells, p_man),
                "catatan": _sv(cells, p_cat),
            }
    rows = []
    for rn in sorted(qa["rows"].keys()):
        if rn == 1:
            continue
        cells = qa["rows"][rn]
        sc = _sv(cells, col_score)
        if not (_is_numeric(sc) and float(sc) < threshold):
            continue
        _id = _sv(cells, col_id)
        user = _sv(cells, col_user)
        key = _id + "||" + user
        pr = prior.get(key, {})
        rows.append({
            "row": rn, "id_trace": _id, "user": user, "bot": _sv(cells, col_bot),
            "intent": _sv(cells, col_intent), "skor": sc,
            "putusan": _sv(cells, col_put), "alasan": _sv(cells, col_alasan),
            "llm": pr.get("llm", ""), "manual": pr.get("manual", ""), "catatan": pr.get("catatan", ""),
        })
    return {"step": 9, "rows": rows, "total": len(rows)}


def step9_save(cfg, ctx):
    edits_raw = ctx.P("edits", "")
    try:
        edits = json.loads(edits_raw)
    except Exception:
        edits = None
    if not isinstance(edits, list):
        raise Exception("Data edit tidak valid.")
    try:
        threshold = float(ctx.P("threshold", "0.6"))
    except Exception:
        threshold = 0.6
    b = step9_base_bytes(cfg, ctx)
    wb = _wb_from_bytes(b)
    if "QA Conf MKTA" not in wb.sheetnames:
        raise Exception('Sheet "QA Conf MKTA" tidak ada.')
    qa = read_sheet(wb["QA Conf MKTA"])
    H = qa["headers"]
    col_id = _find_header(H, ["ID trace", "ID Trace"])
    col_user = _find_header(H, ["user phrase", "User Phrase", "Pertanyaan User"])
    col_bot = _find_header(H, ["bot response", "Bot Response", "Jawaban Bot"])
    col_intent = _find_header(H, ["intent name", "Intent Name", "Intent"])
    col_score = _find_header(H, ["Skor Pemrosesan Bahasa"])
    col_put = _find_header(H, ["PUTUSAN"])
    col_alasan = _find_header(H, ["ALASAN", "Alasan"])
    edit_map = {}
    for e in edits:
        try:
            edit_map[int(e.get("row"))] = e
        except Exception:
            continue
    header = ["ID trace", "user phrase", "bot response", "intent name", "Skor Pemrosesan Bahasa",
             "PUTUSAN", "ALASAN", "Intent Seharusnya (LLM)", "Intent Seharusnya", "Catatan",
             "waktu interaksi", "lang", "insertId", "score"]
    col_waktu = _find_header(H, ["waktu interaksi", "Waktu Interaksi"])
    col_lang = _find_header(H, ["lang", "Lang"])
    col_ins = _find_header(H, ["insertId", "InsertId", "InserId"])
    col_scr = _find_header(H, ["score", "Score"])
    aoa = [header]
    baris = 0
    for rn in sorted(qa["rows"].keys()):
        if rn == 1:
            continue
        cells = qa["rows"][rn]
        sc = _sv(cells, col_score)
        if not (_is_numeric(sc) and float(sc) < threshold):
            continue
        e = edit_map.get(rn, {})
        llm = e.get("llm", "")
        manual = e.get("manual", "")
        catatan = e.get("catatan", "")
        aoa.append([
            _sv(cells, col_id), _sv(cells, col_user), _sv(cells, col_bot), _sv(cells, col_intent),
            (float(sc) if _is_numeric(sc) else sc), _sv(cells, col_put), _sv(cells, col_alasan),
            llm, manual, catatan, _sv(cells, col_waktu), _sv(cells, col_lang),
            _sv(cells, col_ins), _sv(cells, col_scr),
        ])
        baris += 1
    out_bytes = xlsx_upsert_sheet(b, "Analisis MKTA", aoa)
    summary = {"status": "Selesai", "baris_analisis": baris, "threshold": threshold,
               "catatan": 'Sheet "Analisis MKTA" diperbarui.'}
    data = save_artifact(cfg, ctx.run, 9, "xlsx", out_bytes, "hasil_analisis_manual_mkta.xlsx", summary)
    return {"step": 9, "artifact": data, "baris": baris}


# =============================================================
# STEP 10 — Laporan LM & Pembaruan (XLSX + 2 CSV)
# =============================================================
def _csv_bytes(aoa):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    for row in aoa:
        w.writerow(["" if c is None else c for c in row])
    return buf.getvalue().encode("utf-8-sig")


def step10_build(cfg, ctx):
    b = step9_base_bytes(cfg, ctx) if False else None
    state = load_state(cfg, ctx.run)
    s9 = state["steps"].get("9")
    if not s9 or not s9.get("file"):
        raise Exception("Hasil Step 9 belum ada. Jalankan Step 9 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s9["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 9 hilang dari server.")
    with open(p, "rb") as f:
        b = f.read()
    wb = _wb_from_bytes(b)
    if "Analisis MKTA" not in wb.sheetnames:
        raise Exception('Sheet "Analisis MKTA" tidak ada. Jalankan Step 9 dulu.')
    am = read_sheet(wb["Analisis MKTA"])
    H = am["headers"]
    c_id = _find_header(H, ["ID trace", "ID Trace"])
    c_user = _find_header(H, ["user phrase", "Pertanyaan User"])
    c_bot = _find_header(H, ["bot response", "Jawaban Bot"])
    c_intent = _find_header(H, ["intent name", "Intent"])
    c_put = _find_header(H, ["PUTUSAN"])
    c_seharusnya = _find_header(H, ["Intent Seharusnya", "Intent Seharusnya (Manual)"])
    c_llm = _find_header(H, ["Intent Seharusnya (LLM)"])
    c_cat = _find_header(H, ["Catatan"])
    lm_header = ["ID trace", "user phrase", "bot response", "intent name", "PUTUSAN",
                "Intent Seharusnya", "Catatan"]
    pem_header = ["Intent Seharusnya", "Training Phrase Baru"]
    lm_rows = [lm_header]
    pem_map = {}
    for rn in sorted(am["rows"].keys()):
        if rn == 1:
            continue
        cells = am["rows"][rn]
        put = _sv(cells, c_put).strip().upper()
        seharusnya = _sv(cells, c_seharusnya).strip()
        if seharusnya == "":
            seharusnya = _sv(cells, c_llm).strip()
        user = _sv(cells, c_user)
        # hanya baris yang butuh TINDAK LANJUT
        if "TINDAK LANJUT" not in put and put not in ("SALAH", "TIDAK RELEVAN"):
            if seharusnya == "":
                continue
        lm_rows.append([
            _sv(cells, c_id), user, _sv(cells, c_bot), _sv(cells, c_intent),
            _sv(cells, c_put), seharusnya, _sv(cells, c_cat),
        ])
        if seharusnya != "" and user.strip() != "":
            pem_map.setdefault(seharusnya, [])
            if user not in pem_map[seharusnya]:
                pem_map[seharusnya].append(user)
    pem_rows = [pem_header]
    for intent in sorted(pem_map.keys()):
        for phrase in pem_map[intent]:
            pem_rows.append([intent, phrase])
    out_bytes = xlsx_upsert_sheet(b, "LM", lm_rows)
    out_bytes = xlsx_upsert_sheet(out_bytes, "Pembaruan", pem_rows)
    d = run_dir(cfg, ctx.run)
    with open(os.path.join(d, "step10_lm.csv"), "wb") as f:
        f.write(_csv_bytes(lm_rows))
    with open(os.path.join(d, "step10_pembaruan.csv"), "wb") as f:
        f.write(_csv_bytes(pem_rows))
    summary = {"status": "Selesai", "baris_LM": len(lm_rows) - 1, "baris_Pembaruan": len(pem_rows) - 1,
               "catatan": "Excel + CSV LM + CSV Pembaruan siap diunduh."}
    data = save_artifact(cfg, ctx.run, 10, "xlsx", out_bytes, "laporan_LM_dan_pembaruan.xlsx", summary)
    return {"step": 10, "artifact": data, "lm_rows": len(lm_rows) - 1, "pembaruan_rows": len(pem_rows) - 1}


# =============================================================
# STEP 11 — Pembaruan Intent (suntik training phrase -> usersays)
# =============================================================
def s11_derive_phrases(cfg, ctx):
    state = load_state(cfg, ctx.run)
    s10 = state["steps"].get("10")
    if not s10 or not s10.get("file"):
        raise Exception("Hasil Step 10 belum ada. Jalankan Step 10 dulu.")
    p = os.path.join(run_dir(cfg, ctx.run), s10["file"])
    if not os.path.isfile(p):
        raise Exception("File hasil Step 10 hilang dari server.")
    with open(p, "rb") as f:
        b = f.read()
    wb = _wb_from_bytes(b)
    if "Pembaruan" not in wb.sheetnames:
        raise Exception('Sheet "Pembaruan" tidak ada di hasil Step 10.')
    sh = read_sheet(wb["Pembaruan"])
    H = sh["headers"]
    c_intent = _find_header(H, ["Intent Seharusnya", "Intent", "ID"])
    c_phrase = _find_header(H, ["Training Phrase Baru", "Training Phrase", "Phrase"])
    phrases = {}
    for rn in sorted(sh["rows"].keys()):
        if rn == 1:
            continue
        cells = sh["rows"][rn]
        intent = _sv(cells, c_intent).strip()
        phrase = _sv(cells, c_phrase).strip()
        if intent == "" or phrase == "":
            continue
        phrases.setdefault(intent, [])
        if phrase not in phrases[intent]:
            phrases[intent].append(phrase)
    if not phrases:
        raise Exception("Tidak ada training phrase baru pada sheet Pembaruan.")
    return b, phrases


def step11_update(cfg, ctx):
    raw_base = (ctx.P("ngrok_url") or "").strip()
    endpoint = api_endpoint(cfg, raw_base, "/api/update-usersays")
    if not cfg["force_local_api"] and raw_base != "":
        save_ngrok(cfg, ctx.run, raw_base)
    df_zip = ctx.file("df_zip")
    if df_zip is None:
        raise Exception("Unggah ZIP export Dialogflow (df_zip) yang berisi folder intents.")
    _, phrases = s11_derive_phrases(cfg, ctx)
    fields = {"phrases": json.dumps(phrases, ensure_ascii=False)}
    files = {"df_zip": (df_zip[0], df_zip[1] or "dialogflow.zip", "application/zip")}
    res = curl_post_json(cfg, endpoint, files, fields)
    if not isinstance(res, dict) or not res.get("ok", True):
        raise Exception("Server gagal memproses: " + json.dumps(res, ensure_ascii=False)[:400])
    zip_b64 = res.get("zip_b64", "")
    if zip_b64 == "":
        raise Exception("Server tidak mengembalikan ZIP hasil (zip_b64 kosong).")
    import base64
    zip_bytes = base64.b64decode(zip_b64)
    with open(os.path.join(run_dir(cfg, ctx.run), "step11_usersays.zip"), "wb") as f:
        f.write(zip_bytes)
    stats = res.get("stats", {})
    # bangun sheet Status Pembaruan dari report
    report = res.get("report", [])
    st_header = ["Intent", "Status", "Phrase Ditambahkan", "Catatan"]
    st_rows = [st_header]
    if isinstance(report, list):
        for r in report:
            if isinstance(r, dict):
                st_rows.append([r.get("intent", ""), r.get("status", ""),
                                r.get("added", r.get("phrases_added", "")), r.get("note", "")])
    state = load_state(cfg, ctx.run)
    s10 = state["steps"].get("10")
    out_bytes = None
    if s10 and s10.get("file"):
        p = os.path.join(run_dir(cfg, ctx.run), s10["file"])
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out_bytes = xlsx_upsert_sheet(f.read(), "Status Pembaruan", st_rows)
    if out_bytes is None:
        out_bytes = xlsx_build([{"name": "Status Pembaruan", "rows": st_rows}])
    summary = {"status": "Selesai", "statistik": stats,
               "catatan": 'ZIP usersays siap diunduh. Sheet "Status Pembaruan" dibuat.'}
    data = save_artifact(cfg, ctx.run, 11, "xlsx", out_bytes, "status_pembaruan_intent.xlsx", summary)
    return {"step": 11, "artifact": data, "stats": stats}


# =============================================================
# DISPATCH (router — sama dgn switch($action) di index.php)
# =============================================================
def dispatch(action, cfg, ctx):
    handlers = {
        "state": lambda: get_state(cfg, ctx.run),
        "reset": lambda: reset_run(cfg, ctx.run),
        "step1": lambda: step1_pull_logs(cfg, ctx),
        "step2": lambda: step2_json_to_xlsx(cfg, ctx),
        "step3": lambda: step3_training_intent(cfg, ctx),
        "step4": lambda: step4_analyze(cfg, ctx),
        "step5": lambda: step5_qwen_judge(cfg, ctx),
        "step6load": lambda: step6_load(cfg, ctx),
        "step6": lambda: step6_save(cfg, ctx),
        "step7": lambda: step7_mkta(cfg, ctx),
        "step8load": lambda: step8_load(cfg, ctx),
        "step8": lambda: step8_run(cfg, ctx),
        "step9load": lambda: step9_load(cfg, ctx),
        "step9": lambda: step9_save(cfg, ctx),
        "step10": lambda: step10_build(cfg, ctx),
        "step11": lambda: step11_update(cfg, ctx),
        "step12": lambda: avaya1_upload_json(cfg, ctx),
        "step13": lambda: avaya2_pull_intents(cfg, ctx),
        "step14": lambda: avaya3_analyze(cfg, ctx),
        "step14start": lambda: avaya3_start(cfg, ctx),
        "step14progress": lambda: avaya3_progress(cfg, ctx),
        "step14fetch": lambda: avaya3_fetch(cfg, ctx),
        "step15": lambda: avaya4_dashboard(cfg, ctx),
        "step16": lambda: avaya5_excel(cfg, ctx),
        "avayadiag": lambda: avaya_diag(cfg, ctx),
    }
    fn = handlers.get(action)
    if fn is None:
        raise Exception("Aksi tidak dikenal: %s" % action)
    return fn()


# =============================================================
# DOWNLOAD (pengganti handle_download)
# =============================================================
def handle_download(cfg, request: Request):
    q = request.query_params
    run = q.get("run", "")
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", run):
        return PlainTextResponse("Run ID tidak valid.", status_code=400)
    part = q.get("part", "")
    d = run_dir(cfg, run, create=False)

    def file_resp(path, mime, filename=None):
        with open(path, "rb") as f:
            data = f.read()
        headers = {}
        if filename:
            headers["Content-Disposition"] = 'attachment; filename="%s"' % filename.replace('"', "")
        return Response(content=data, media_type=mime, headers=headers)

    if part in ("lm", "pembaruan"):
        m = {"lm": ("step10_lm.csv", "LM.csv"), "pembaruan": ("step10_pembaruan.csv", "Pembaruan.csv")}
        p = os.path.join(d, m[part][0])
        if not os.path.isfile(p):
            return PlainTextResponse("File CSV belum dibuat. Jalankan Step 10.", status_code=404)
        return file_resp(p, "text/csv; charset=utf-8", m[part][1])
    if part == "zip11":
        p = os.path.join(d, "step11_usersays.zip")
        if not os.path.isfile(p):
            return PlainTextResponse("ZIP belum dibuat. Jalankan Step 11.", status_code=404)
        return file_resp(p, "application/zip", "usersays_updated.zip")
    if part == "avayadash":
        p = os.path.join(d, "step15_dashboard.html")
        if not os.path.isfile(p):
            return PlainTextResponse("Dashboard belum dibuat. Jalankan Step 15.", status_code=404)
        with open(p, "rb") as f:
            return Response(content=f.read(), media_type="text/html; charset=utf-8")
    try:
        n = int(q.get("step", "0"))
    except Exception:
        n = 0
    state = load_state(cfg, run)
    step = state["steps"].get(str(n))
    if not step or not step.get("file"):
        return PlainTextResponse("File tidak ditemukan.", status_code=404)
    p = os.path.join(d, step["file"])
    if not os.path.isfile(p):
        return PlainTextResponse("File hilang dari server.", status_code=404)
    return file_resp(p, step.get("mime", "application/octet-stream"), str(step.get("name", "")))


# =============================================================
# ROUTE UTAMA — GET/POST / (ENDPOINT = location.pathname)
# =============================================================
def _load_html(name):
    p = os.path.join(BASE_DIR, "templates", name)
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


PAGE_HTML = None    # landing page (index.html)
TOOLS_HTML = None   # halaman tool analisis (tools.html, dulu index.php)


def chat_llm(messages):
    """Balas chat memakai LLM cloud (OpenAI/Gemini/Azure) via llm_client.
    Menyuntik konteks pengetahuan analis (glosarium/disambiguasi/peta intent)
    yang relevan dengan pesan terakhir user."""
    system = (
        "Kamu asisten AI berbahasa Indonesia yang membantu, ramah, ringkas, dan "
        "akurat. Gunakan format Markdown bila relevan."
    )
    try:
        last_user = ""
        for m in reversed(messages or []):
            if (m.get("role") or "").lower() == "user":
                last_user = m.get("content") or ""
                break
        system += kctx.system_suffix(last_user)
    except Exception:
        pass
    # Masking PII pada seluruh pesan + system sebelum ke LLM cloud (Fase A).
    safe_messages = [dict(m, content=pii_mask.mask_text(m.get("content")))
                     for m in (messages or [])]
    return llm_client.chat(safe_messages, system=pii_mask.mask_text(system),
                           max_new_tokens=1024, temperature=0.2)


@app.get("/")
async def landing(request: Request):
    """Landing page: chat + akses tools."""
    return render_page(request, "index.html", "")


@app.api_route("/tools", methods=["GET", "POST"])
async def tools(request: Request):
    """Tool analisis Dialogflow + Avaya (Step 1..16)."""
    global TOOLS_HTML
    action = request.query_params.get("action", "")
    if action == "":
        return render_page(request, "tools.html", "tools")
    if action == "download":
        return await run_in_threadpool(handle_download, CONFIG, request)
    try:
        ctx = await build_ctx(request)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    try:
        # dispatch memakai requests (blocking) untuk proxy ke backend; jalankan di
        # threadpool supaya TIDAK membekukan event loop (bikin halaman muter).
        result = await run_in_threadpool(dispatch, action, CONFIG, ctx)
        if not isinstance(result, dict):
            result = {"result": result}
        if "ok" not in result:
            result["ok"] = True
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/config")
async def api_config():
    provider = (os.environ.get("LLM_PROVIDER", "openai") or "openai").strip().lower()
    if provider.startswith("azure"):
        model = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
        label = "Azure OpenAI"
    elif provider in ("gemini", "google"):
        model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
        label = "Google Gemini"
    else:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        label = "OpenAI"
    return {"ok": True, "provider": provider, "model": model, "label": label}


@app.post("/api/chat")
async def api_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list) or not messages:
        return JSONResponse({"ok": False, "error": "messages kosong."})
    messages = messages[-20:]  # batasi konteks agar hemat token
    try:
        reply = await run_in_threadpool(chat_llm, messages)
        return JSONResponse({"ok": True, "reply": reply})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# =============================================================
# SISTEM ANALITIK: dashboard, ingest periodik, AI tanya-jawab data
# =============================================================
DASHBOARD_HTML = None


def analytics_summary(preset, start, end, lang=None, inc_system=False, inc_umum=False):
    """Kumpulan metrik untuk dashboard, dijalankan di threadpool."""
    s, e = adb.resolve_range(preset, start, end)
    conn = adb.init_db(adb.connect())
    try:
        cov = adb.range_status(conn, s, e, (lang or "id")) if (s and e) else None
        return {
            "ok": True,
            "range": {"preset": preset or "", "start": s, "end": e},
            "lang": lang or "",
            "inc_system": bool(inc_system),
            "inc_umum": bool(inc_umum),
            "coverage": cov,
            "overview": adb.overview(conn, s, e, lang=lang, include_system=inc_system, include_umum=inc_umum),
            "top_intents": adb.top_intents(conn, s, e, 100, include_system=inc_system, include_umum=inc_umum, lang=lang),
            "volume": adb.volume_by_day(conn, s, e, lang=lang),
            "new_questions": adb.new_questions(conn, s, e, 200, lang=lang),
            "hot_topics": adb.hot_topics(conn, s, e, 20, lang=lang),
            "bounds": adb.data_bounds(conn),
            "last_ingest": adb.get_meta(conn, "last_ingest_at"),
            "last_range": adb.get_meta(conn, "last_ingest_range"),
        }
    finally:
        conn.close()


def _extract_sql(raw):
    raw = (raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            j = json.loads(m.group(0))
            if isinstance(j, dict) and j.get("sql"):
                return str(j["sql"]).strip()
        except Exception:
            pass
    m = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(select\b.+)", raw, re.S | re.I)
    if m:
        return m.group(1).strip()
    return raw


def answer_data_question(question):
    """AI tanya-jawab data: text-to-SQL read-only + rangkum jawaban natural."""
    conn = adb.init_db(adb.connect())
    try:
        sys1 = (
            "Kamu ahli SQLite. Ubah pertanyaan pengguna menjadi SATU query "
            "SELECT untuk menjawabnya. Balas HANYA JSON {\"sql\":\"...\"} tanpa "
            "penjelasan, tanpa markdown.\n" + adb.SCHEMA_FOR_LLM +
            "\nHari ini (Asia/Jakarta): " + adb._jkt_today() +
            ". Untuk 'minggu lalu' gunakan rentang tanggal pada kolom day. "
            "Selalu tambahkan LIMIT yang wajar."
        )
        raw = llm_client.chat([{"role": "user", "content": pii_mask.mask_text(question)}],
                              system=sys1, max_new_tokens=400, temperature=0.0)
        sql = _extract_sql(raw)
        res = adb.run_select(conn, sql)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "Query gagal."),
                    "sql": res.get("sql", sql)}
        preview = json.dumps({"columns": res["columns"], "rows": res["rows"][:50]},
                             ensure_ascii=False)
        sys2 = (
            "Jawab pertanyaan pengguna dalam Bahasa Indonesia secara ringkas, "
            "jelas, dan enak dibaca berdasarkan HASIL query di bawah. Sebutkan "
            "angka penting. Jangan mengarang data di luar hasil."
        )
        sys2 += kctx.system_suffix(question)
        answer = llm_client.chat(
            [{"role": "user", "content": pii_mask.mask_text("Pertanyaan: " + question +
              "\n\nHasil query (JSON):\n" + preview)}],
            system=pii_mask.mask_text(sys2), max_new_tokens=700, temperature=0.2)
        return {"ok": True, "answer": answer, "sql": res.get("sql", sql),
                "columns": res["columns"], "rows": res["rows"][:50]}
    finally:
        conn.close()


@app.get("/awe")
async def awe_page(request: Request):
    """Menu Analisis AWE Avaya (terpisah dari Dialogflow)."""
    return render_page(request, "awe.html", "awe")


@app.get("/api/awe/runs")
async def awe_list_runs():
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return {"ok": True, "runs": avdb.list_runs(conn), "stats": avdb.stats(conn)}
        finally:
            conn.close()
    return JSONResponse(await run_in_threadpool(_do))


@app.get("/api/awe/run")
async def awe_get_run(id: str = ""):
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.get_run(conn, id, with_records=False)
        finally:
            conn.close()
    r = await run_in_threadpool(_do)
    if not r:
        return JSONResponse({"ok": False, "error": "Analisis tidak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "run": r})


@app.get("/awe/dashboard")
async def awe_run_dashboard(id: str = ""):
    def _get():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.get_run(conn, id)
        finally:
            conn.close()
    r = await run_in_threadpool(_get)
    if not r:
        return PlainTextResponse("Analisis tidak ditemukan.", status_code=404)
    endpoint = api_endpoint(CONFIG, "", "/api/avaya-render")
    payload = json.dumps({"dashboard": r["dashboard"]}, ensure_ascii=False)
    try:
        html = await run_in_threadpool(curl_json_raw, CONFIG, endpoint, payload)
        return Response(content=html, media_type="text/html; charset=utf-8")
    except Exception as _e:
        return PlainTextResponse("Backend AWE belum aktif (jalankan avaya_pipeline di :8000). Detail: %r" % _e, status_code=502)


@app.post("/api/awe/delete")
async def awe_delete_run(request: Request):
    body = await request.json()
    rid = (body or {}).get("id", "")
    def _do():
        conn = avdb.init_db(avdb.connect())
        try:
            return avdb.delete_run(conn, rid)
        finally:
            conn.close()
    n = await run_in_threadpool(_do)
    return JSONResponse({"ok": True, "deleted": n})


@app.get("/dashboard")
async def dashboard(request: Request):
    return render_page(request, "dashboard.html", "dashboard")


@app.get("/api/analytics/summary")
async def api_analytics_summary(request: Request):
    q = request.query_params
    preset = q.get("range", "7d")
    start = q.get("start") or None
    end = q.get("end") or None
    lang = q.get("lang") or None
    inc_system = (q.get("inc_system") in ("1", "true", "on"))
    inc_umum = (q.get("inc_umum") in ("1", "true", "on"))
    try:
        return JSONResponse(await run_in_threadpool(analytics_summary, preset, start, end, lang, inc_system, inc_umum))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/analytics/search-intents")
async def api_search_intents(request: Request):
    term = request.query_params.get("q", "").strip()
    if not term:
        return JSONResponse({"ok": False, "error": "Parameter q wajib."})

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "term": term, "results": adb.search_intents(conn, term, 25)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# =============================================================
# FASE 2 — Analisis Deflection & Sesi (Epik D1-D3, D6)
# =============================================================
@app.get("/deflection")
async def deflection_page(request: Request):
    return render_page(request, "deflection.html", "deflection")


def _defl_range(q):
    preset = q.get("range", "30d")
    start = q.get("start") or None
    end = q.get("end") or None
    lang = q.get("lang") or None
    s, e = adb.resolve_range(preset, start, end)
    return s, e, lang


@app.get("/api/deflection/summary")
async def api_deflection_summary(request: Request):
    q = request.query_params
    s, e, lang = _defl_range(q)
    try:
        ws = int(q.get("work_start", 8))
        we = int(q.get("work_end", 16))
    except Exception:
        ws, we = 8, 16
    wd_raw = q.get("work_days", "1,2,3,4,5")
    try:
        wd = tuple(int(x) for x in wd_raw.split(",") if x.strip() != "")
    except Exception:
        wd = (1, 2, 3, 4, 5)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            data = adb.deflection_overview(conn, s, e, lang, wd, ws, we)
            data["ok"] = True
            data["range"] = {"start": s, "end": e}
            return data
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@app.get("/api/deflection/candidates")
async def api_deflection_candidates(request: Request):
    q = request.query_params
    s, e, lang = _defl_range(q)
    try:
        limit = int(q.get("limit", 200))
    except Exception:
        limit = 200

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "items": adb.candidate_list(conn, s, e, lang, limit=limit)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@app.get("/api/deflection/candidate")
async def api_deflection_candidate(request: Request):
    q = request.query_params
    phrase = q.get("phrase", "")
    if not phrase.strip():
        return JSONResponse({"ok": False, "error": "Parameter phrase wajib."})
    s, e, lang = _defl_range(q)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "detail": adb.candidate_detail(conn, phrase, s, e, lang)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@app.get("/api/deflection/transcript")
async def api_deflection_transcript(request: Request):
    sid = request.query_params.get("session_id", "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "Parameter session_id wajib."})

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return {"ok": True, "session_id": sid, "turns": adb.session_transcript(conn, sid)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@app.post("/api/deflection/status/save")
async def api_deflection_status_save(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    phrase = (body.get("phrase") or "").strip()
    if not phrase:
        return JSONResponse({"ok": False, "error": "phrase kosong."})
    status = (body.get("status") or "").strip().lower()
    note = body.get("note") or ""
    _u = getattr(request.state, "user", None) or {}
    who = (_u.get("nama") or _u.get("username") or "").strip()

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            return adb.set_candidate_status(conn, phrase, status, note, who)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as ex:
        return JSONResponse({"ok": False, "error": str(ex)})


@app.post("/api/ingest")
async def api_ingest(request: Request):
    """Tarik data ke database (dipakai halaman Kelola Data). Ingest PINTAR:
    hanya menarik hari yang belum ada / belum lengkap, kecuali force=true."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    lang = (body.get("lang") or "id").strip().lower()
    if lang not in ("id", "en"):
        lang = "id"
    force = bool(body.get("force"))
    preset = (body.get("range") or "").strip().lower()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    if preset in ("now", "today"):
        s, e = adb.resolve_range("today")
    elif preset == "yesterday":
        s, e = adb.resolve_range("yesterday")
    elif preset in ("7d", "30d", "90d"):
        s, e = adb.resolve_range(preset)
    elif preset == "all":
        return JSONResponse({"ok": False,
                             "error": "Pilih rentang tanggal spesifik untuk menarik data."})
    else:
        s, e = (start or None), (end or start or None)
    if not s or not e:
        return JSONResponse({"ok": False, "error": "Rentang tanggal tidak valid."})
    try:
        res = await run_in_threadpool(ingest.ensure_range, s, e, lang, None, force, False)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


def _parse_log_bytes(data):
    # bytes -> list entri log (JSON array / objek / JSON Lines / pembungkus
    # {"entries":[...]} atau {"data":[...]}). Sama dengan logika Step 2.
    decoded = data.decode("utf-8", "replace")
    if decoded.startswith("\ufeff"):
        decoded = decoded[1:]
    decoded = decoded.strip()
    if not decoded:
        return []
    items = []
    parsed = None
    try:
        parsed = json.loads(decoded)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        if isinstance(parsed.get("entries"), list):
            items = parsed["entries"]
        elif isinstance(parsed.get("data"), list):
            items = parsed["data"]
        else:
            items = [parsed]
    else:
        for line in re.split(r"\r?\n", decoded):
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except Exception:
                continue
            if isinstance(v, list):
                items.extend(v)
            elif isinstance(v, dict):
                items.append(v)
    return [it for it in items if isinstance(it, dict)]


def _parse_log_upload(data, name):
    # bytes (satu file) -> list entri. Mendukung .zip berisi banyak file JSON.
    low = (name or "").lower()
    is_zip = low.endswith(".zip") or (len(data) >= 2 and data[:2] == b"PK")
    if is_zip:
        out = []
        zf = zipfile.ZipFile(io.BytesIO(data))
        for info in zf.infolist():
            if info.is_dir():
                continue
            nm = info.filename.lower()
            if not (nm.endswith(".json") or nm.endswith(".jsonl")
                    or nm.endswith(".ndjson") or nm.endswith(".txt")):
                continue
            out.extend(_parse_log_bytes(zf.read(info)))
        return out
    return _parse_log_bytes(data)


@app.post("/api/ingest-upload")
async def api_ingest_upload(request: Request):
    # Impor manual: unggah file JSON hasil ekspor Google Cloud Logging langsung
    # ke analytics.db (untuk uji end-to-end / data lampau tanpa akses Google).
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"ok": False, "error": "Form tidak valid."})
    ups = [u for u in form.getlist("file") if isinstance(u, StarletteUploadFile)]
    if not ups:
        return JSONResponse({"ok": False, "error": "Tidak ada file diunggah."})
    lang = (form.get("lang") or "").strip().lower()
    if lang not in ("id", "en"):
        lang = None  # auto: baca lang dari tiap payload
    entries = []
    errors = []
    for u in ups:
        try:
            data = await u.read()
            entries.extend(_parse_log_upload(data, u.filename or "log.json"))
        except Exception as e:
            errors.append("%s: %s" % ((u.filename or "file"), str(e)[:200]))
    if not entries:
        msg = "Tidak ada entri log yang bisa dibaca."
        if errors:
            msg += " " + " | ".join(errors)
        return JSONResponse({"ok": False, "error": msg})
    try:
        res = await run_in_threadpool(ingest.ingest_entries, entries, lang, None, False)
        if errors:
            res["warnings"] = errors
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})



DATA_HTML = None


@app.get("/data")
async def data_page(request: Request):
    return render_page(request, "data.html", "data")


@app.get("/api/data/status")
async def api_data_status(request: Request):
    """Status kelengkapan data per hari untuk rentang (halaman Kelola Data)."""
    q = request.query_params
    lang = (q.get("lang") or "id").strip().lower()
    if lang not in ("id", "en"):
        lang = "id"
    preset = (q.get("range") or "").strip().lower()
    start = q.get("start") or None
    end = q.get("end") or None
    if preset and preset not in ("", "custom"):
        start, end = adb.resolve_range(preset)

    def _run():
        conn = adb.init_db(adb.connect())
        try:
            rs = adb.range_status(conn, start, end, lang) if (start and end) else None
            return {"ok": True, "lang": lang, "status": rs,
                    "bounds": adb.data_bounds(conn),
                    "last_ingest": adb.get_meta(conn, "last_ingest_at"),
                    "today": adb._jkt_today()}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/ask-data")
async def api_ask_data(request: Request):
    """AI menjawab pertanyaan tentang data (text-to-SQL, read-only)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = (body.get("question") if isinstance(body, dict) else "") or ""
    question = question.strip()
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        return JSONResponse(await run_in_threadpool(answer_data_question, question))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# =============================================================
# EPIK B: "Tanya AI" kontekstual di SEMUA menu.
# Satu endpoint /api/ask; perilaku ditentukan oleh halaman (page):
#   - Halaman berbasis DATA  -> text-to-SQL read-only atas tabel interactions
#     (dipakai ulang answer_data_question, sama seperti Dashboard).
#   - Halaman berbasis PUSTAKA -> jawaban BERPAGAR: hanya dari database internal
#     pustaka (Glosarium/Disambiguasi/Peta Intent/Katalog); dilarang memakai
#     pengetahuan umum/eksternal, dan wajib mengaku bila info tak tersedia.
# =============================================================
ASK_DATA_PAGES = {"data", "deflection", "lifecycle", "tools", "dashboard"}
ASK_KNOWLEDGE_PAGES = {"glosarium", "disambiguasi", "intentmap"}

ASK_GUARDRAIL = (
    "Kamu asisten internal Camerad Studio untuk tim analis DJP. Jawab HANYA "
    "berdasarkan KONTEKS INTERNAL di bawah, yang berasal dari database internal "
    "tim. DILARANG memakai pengetahuan umum/eksternal, mencari di web, atau "
    "menebak-nebak. Jika informasi yang diminta TIDAK ADA di dalam konteks, "
    "jawab jujur: \"Maaf, informasi itu belum tersedia di data internal untuk "
    "halaman ini.\" Jawab ringkas, jelas, dalam Bahasa Indonesia, boleh Markdown."
)


def build_page_context(page, question, lang=None, max_chars=2600):
    """Penyedia konteks per-halaman: tiap menu pustaka menyuplai potongan
    datanya sendiri dari database internal. Mengembalikan blok teks (bisa
    kosong). Semua kegagalan ditangani diam-diam supaya endpoint tetap jalan."""
    page = (page or "").strip().lower()
    q = (question or "").strip()
    blocks = []
    try:
        if page == "glosarium":
            c = gdb.init_db(gdb.connect())
            try:
                m = gdb.match(c, q, limit=6)
                txt = gdb.build_context_text(m) if m else ""
                if not (txt and txt.strip()):
                    terms = gdb.list_terms(c, q=(q or None), limit=40, lang=lang)
                    names = [t.get("term") for t in terms if t.get("term")]
                    txt = ("Tidak ada entri yang cocok persis. Total %d istilah "
                           "di Glosarium. Contoh istilah tersedia: %s"
                           % (gdb.count(c), ", ".join(names[:40]) or "-"))
                blocks.append("[Glosarium Istilah Pajak]\n" + txt)
            finally:
                c.close()
        elif page == "disambiguasi":
            c = ddb.init_db(ddb.connect())
            try:
                m = ddb.match(c, q, limit=6)
                txt = ddb.build_context_text(m) if m else ""
                if not (txt and txt.strip()):
                    rules = ddb.list_rules(c, q=(q or None), limit=40, lang=lang)
                    names = [r.get("pemicu") for r in rules if r.get("pemicu")]
                    txt = ("Tidak ada aturan yang cocok persis. Total %d aturan "
                           "disambiguasi. Contoh pemicu tersedia: %s"
                           % (ddb.count(c), ", ".join(names[:40]) or "-"))
                blocks.append("[Pustaka Disambiguasi]\n" + txt)
            finally:
                c.close()
        elif page == "intentmap":
            c = imdb.init_db(imdb.connect())
            try:
                m = imdb.match(c, q, limit=5)
                mc = imdb.match_catalog(c, q, limit=5)
                t1 = imdb.build_context_text(m) if m else ""
                t2 = imdb.build_catalog_context_text(mc) if mc else ""
                combined = "\n\n".join([t for t in (t1, t2) if t and t.strip()])
                if not combined:
                    combined = ("Tidak ada entri Peta Intent / Katalog yang cocok "
                                "dengan pertanyaan.")
                blocks.append("[Peta Intent & Katalog]\n" + combined)
            finally:
                c.close()
    except Exception:
        pass
    body = "\n\n".join(b for b in blocks if b and b.strip())
    if max_chars and len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\u2026"
    return body


def answer_knowledge_question(page, question, lang=None):
    """Jawaban berpagar untuk halaman pustaka (Glosarium/Disambiguasi/Intent)."""
    ctx = build_page_context(page, question, lang)
    system = ASK_GUARDRAIL
    if ctx:
        system += ("\n\n=== KONTEKS INTERNAL HALAMAN ===\n" + ctx +
                   "\n=== AKHIR KONTEKS INTERNAL ===")
    # Tambah konteks silang dari pustaka lain yang relevan (glosarium/disambig/
    # peta intent/katalog) agar jawaban tetap konsisten lintas menu.
    system += kctx.system_suffix(question)
    answer = llm_client.chat([{"role": "user", "content": pii_mask.mask_text(question)}],
                             system=pii_mask.mask_text(system), max_new_tokens=800, temperature=0.1)
    return {"ok": True, "mode": "knowledge", "answer": answer,
            "has_context": bool(ctx)}


@app.post("/api/ask")
async def api_ask(request: Request):
    """Tanya AI kontekstual per-halaman. Body: {question, page, lang?}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    page = (body.get("page") or "").strip().lower()
    lang = body.get("lang") or None
    if not question:
        return JSONResponse({"ok": False, "error": "question kosong."})
    try:
        if page in ASK_KNOWLEDGE_PAGES:
            return JSONResponse(await run_in_threadpool(
                answer_knowledge_question, page, question, lang))
        # Default & halaman data: text-to-SQL read-only (sama seperti Dashboard).
        res = await run_in_threadpool(answer_data_question, question)
        if isinstance(res, dict) and "mode" not in res:
            res["mode"] = "data"
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


GLOSSARY_HTML = None


@app.get("/glossary")
async def glossary_page(request: Request):
    return render_page(request, "glossary.html", "glossary")


@app.get("/api/glossary/list")
async def api_glossary_list(request: Request):
    """Daftar istilah (dengan pencarian & filter). Auto-seed contoh saat kosong."""
    q = request.query_params

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            if gdb.count(conn) == 0:
                gdb.seed_defaults(conn)
            items = gdb.list_terms(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                sistem=(q.get("sistem") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "glosarium")
            return {"ok": True, "items": items, "total": gdb.count(conn),
                    "kategori": gdb.KATEGORI, "sistem": gdb.SISTEM, "status": gdb.STATUS}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/glossary/save")
async def api_glossary_save(request: Request):
    """Tambah atau perbarui satu istilah. Divalidasi di glossary_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            return gdb.upsert_term(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/glossary/delete")
async def api_glossary_delete(request: Request):
    """Hapus satu istilah berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not gid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            return {"ok": gdb.delete_term(conn, gid), "id": gid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


DISAMBIG_HTML = None


@app.get("/disambig")
async def disambig_page(request: Request):
    return render_page(request, "disambig.html", "disambig")


@app.get("/api/disambig/list")
async def api_disambig_list(request: Request):
    """Daftar aturan disambiguasi (dengan pencarian & filter). Auto-seed saat kosong."""
    q = request.query_params

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            if ddb.count(conn) == 0:
                ddb.seed_defaults(conn)
            items = ddb.list_rules(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "disambiguasi")
            return {"ok": True, "items": items, "total": ddb.count(conn),
                    "kategori": ddb.KATEGORI, "sistem": ddb.SISTEM, "status": ddb.STATUS,
                    "default_cutoff": ddb.DEFAULT_CUTOFF}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/disambig/save")
async def api_disambig_save(request: Request):
    """Tambah atau perbarui satu aturan. Divalidasi di disambig_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            return ddb.upsert_rule(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/disambig/delete")
async def api_disambig_delete(request: Request):
    """Hapus satu aturan berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    rid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not rid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            return {"ok": ddb.delete_rule(conn, rid), "id": rid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


INTENTMAP_HTML = None


@app.get("/intentmap")
async def intentmap_page(request: Request):
    return render_page(request, "intentmap.html", "intentmap")


@app.get("/api/intentmap/list")
async def api_intentmap_list(request: Request):
    """Daftar kebijakan intent (dengan pencarian & filter). Auto-seed saat kosong."""
    q = request.query_params

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            if imdb.count(conn) == 0:
                imdb.seed_defaults(conn)
            items = imdb.list_intents(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                struktur=(q.get("struktur") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "intentmap")
            return {"ok": True, "items": items, "total": imdb.count(conn),
                    "kategori": imdb.KATEGORI, "struktur": imdb.STRUKTUR, "status": imdb.STATUS,
                    "prioritas": imdb.PRIORITAS_LABELS}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/save")
async def api_intentmap_save(request: Request):
    """Tambah atau perbarui satu kebijakan intent. Divalidasi di intentmap_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return imdb.upsert_intent(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/delete")
async def api_intentmap_delete(request: Request):
    """Hapus satu kebijakan intent berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    iid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not iid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return {"ok": imdb.delete_intent(conn, iid), "id": iid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/intentmap/catalog")
async def api_intentmap_catalog(request: Request):
    """Daftar Katalog Intent (deskripsi AI/analis) + statistik ringkas."""
    q = request.query_params

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            items = imdb.catalog_list(conn, q=(q.get("q") or None),
                                      filt=(q.get("filter") or "all"),
                                      lang=(q.get("lang") or None),
                                      limit=imdb._to_int(q.get("limit"), 500))
            _enrich_dipakai(items, "katalog")
            return {"ok": True, "items": items, "stats": imdb.catalog_stats(conn)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/lifecycle")
async def lifecycle_page(request: Request):
    return render_page(request, "lifecycle.html", "lifecycle")


def _lc_months(v, d=6):
    try:
        n = int(float(v))
        return n if n > 0 else d
    except Exception:
        return d


def _lc_audit_user(request):
    u = getattr(request.state, "user", None)
    if u is None:
        return ""
    return (getattr(u, "nama", None) or getattr(u, "username", None) or "")


@app.get("/api/lifecycle/summary")
async def api_lifecycle_summary(request: Request):
    """Ringkasan siklus-hidup intent (Epik E). Menyegarkan last_called_at dari interactions."""
    q = request.query_params
    months = _lc_months(q.get("months"), 6)
    do_refresh = str(q.get("refresh") or "1").lower() not in ("0", "false", "no", "")

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            refreshed = None
            if do_refresh:
                try:
                    refreshed = imdb.refresh_lifecycle(conn)
                except Exception as _e:
                    refreshed = {"error": str(_e)}
            return {"ok": True, "overview": imdb.lifecycle_overview(conn, retensi_bulan=months),
                    "refreshed": refreshed}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/lifecycle/list")
async def api_lifecycle_list(request: Request):
    """Daftar intent + status siklus-hidup (dipanggil / tidak / kandidat retensi / soft-deleted)."""
    q = request.query_params
    months = _lc_months(q.get("months"), 6)

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            items = imdb.lifecycle_list(
                conn,
                filt=(q.get("filter") or "all"),
                q=(q.get("q") or None),
                lang=(q.get("lang") or None),
                limit=imdb._to_int(q.get("limit"), 1000),
                retensi_bulan=months,
            )
            return {"ok": True, "items": items,
                    "overview": imdb.lifecycle_overview(conn, retensi_bulan=months)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/lifecycle/softdelete/save")
async def api_lifecycle_softdelete_save(request: Request):
    """Tandai/pulihkan soft-delete intent (butuh peran dengan hak edit). Body: {id|intent, soft_deleted}."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})
    ident = ((body.get("id") or body.get("intent") or "")).strip()
    deleted = bool(body.get("soft_deleted", True))
    uname = _lc_audit_user(request)

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return imdb.set_soft_delete(conn, ident, deleted=deleted, user=uname)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/describe")
async def api_intentmap_describe(request: Request):
    """Deskripsi AI (draf) utk sebagian intent yg belum dideskripsikan.
    Prioritas: paling sering dipanggil dulu. Batas per batch <=500."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        limit = int(body.get("limit") or 50)
    except Exception:
        limit = 50
    limit = max(1, min(500, limit))
    only_called = bool(body.get("only_called", True))

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            hasil = idesc.run_describe_batch(conn, limit=limit, only_called=only_called)
            return {"ok": True, "hasil": hasil, "stats": imdb.catalog_stats(conn)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/approve")
async def api_intentmap_approve(request: Request):
    """Setujui/koreksi deskripsi -> terverifikasi (dikunci dari timpa AI)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    iid = ((body.get("id") or "")).strip()
    if not iid:
        return JSONResponse({"ok": False, "error": "id kosong."})
    edits = {}
    for kk in ("deskripsi_maksud", "deskripsi_cakupan"):
        v = body.get(kk)
        if isinstance(v, str) and v.strip():
            edits[kk] = v.strip()
    if isinstance(body.get("sistem_tersinggung"), list):
        edits["sistem_tersinggung"] = body.get("sistem_tersinggung")

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            _u = getattr(request.state, "user", None) or {}
            _approver = (_u.get("nama") or _u.get("username") or "").strip()
            res = imdb.approve_description(conn, iid, edits=(edits or None), disetujui_oleh=_approver)
            if isinstance(res, dict):
                res["stats"] = imdb.catalog_stats(conn)
            return res
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/describe/start")
async def api_intentmap_describe_start(request: Request):
    """Mulai draf AI latar-belakang (lazy/bertahap) utk SEMUA sisa intent.
    Aman utk ~1.300 intent: berjalan di thread, resumable, tak memblokir request."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    only_called = bool(body.get("only_called", False))
    try:
        chunk = int(body.get("chunk") or 25)
    except Exception:
        chunk = 25
    try:
        max_items = int(body["max_items"]) if body.get("max_items") not in (None, "") else None
    except Exception:
        max_items = None
    try:
        sleep_s = float(body.get("sleep_s") or 0)
    except Exception:
        sleep_s = 0.0

    def _connect():
        return imdb.init_db(imdb.connect())

    def _run():
        res = idesc.start_background_drain(_connect, chunk=chunk, sleep_s=sleep_s,
                                           max_items=max_items, only_called=only_called)
        try:
            conn = _connect()
            try:
                res["stats"] = imdb.catalog_stats(conn)
            finally:
                conn.close()
        except Exception:
            pass
        return res
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/intentmap/describe/progress")
async def api_intentmap_describe_progress(request: Request):
    """Progres job draf AI latar-belakang + statistik katalog terkini."""
    def _run():
        prog = idesc.describe_progress()
        try:
            conn = imdb.init_db(imdb.connect())
            try:
                prog["stats"] = imdb.catalog_stats(conn)
            finally:
                conn.close()
        except Exception:
            pass
        return {"ok": True, "progress": prog}
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/intentmap/describe/stop")
async def api_intentmap_describe_stop(request: Request):
    """Minta hentikan job draf AI latar-belakang (berhenti setelah batch berjalan)."""
    def _run():
        return idesc.stop_background_drain()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


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


def _enrich_dipakai(items, pustaka):
    """Sisipkan field dipakai (jumlah pemakaian) ke tiap item daftar."""
    try:
        _pc = pstats.init_db(pstats.connect())
        try:
            um = pstats.usage_map(_pc, pustaka)
        finally:
            _pc.close()
    except Exception:
        um = {}
    for it in (items or []):
        try:
            it["dipakai"] = int(um.get(it.get("id"), 0))
        except Exception:
            it["dipakai"] = 0
    return items


@app.get("/api/pustaka/stats")
async def api_pustaka_stats(request: Request):
    """Statistik pemakaian pustaka pengetahuan (berapa sering tiap entri dipakai)."""
    def _run():
        conn = pstats.init_db(pstats.connect())
        try:
            return {"ok": True, "stats": pstats.stats(conn, top_n=8)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "dialogflow-avaya-pipeline-frontend"}


# =============================================================
# Studio Dokumen (Epik C) — daftarkan route dari modul terpisah
# =============================================================
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
