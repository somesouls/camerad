# -*- coding: utf-8 -*-
"""db/menu_catalog.py — Granularitas akses PER-TAUTAN MENU (per-link RBAC).

ADITIF & kompatibel mundur terhadap RBAC area lama (db/users_db.py):
- Katalog setiap tautan menu sidebar (templates/base.html) -> {key, group, label, path}.
- Izin per-menu disimpan di tabel SENDIRI (role_menus, user_menu_grants) sehingga
  db/users_db.py TIDAK perlu diubah sama sekali.
- menu_allowed() menentukan tampil/tidaknya menu untuk (peran, user):
    * Bila peran BELUM dikonfigurasi per-menu -> jatuh ke izin area lama
      (users_db.area_allowed) => perilaku IDENTIK dengan sebelum fitur ini.
    * Bila sudah dikonfigurasi -> hanya menu tercentang yang tampil.
- Enforcement API tetap memakai area coarse di app_core (tak berubah). Fitur ini
  fokus pada granularitas VISIBILITAS + akses halaman menu per jenis user.
"""
import db.users_db as usr

# --- Grup accordion sidebar (mengikuti templates/base.html) -------------------
MENU_GROUPS = [
    {"key": "rag_chatbot", "label": "RAG Chatbot"},
    {"key": "rag_agent",   "label": "RAG Agent"},
    {"key": "dialogflow",  "label": "Dialogflow"},
    {"key": "awe_chat",    "label": "AWE Chat"},
    {"key": "awe_phone",   "label": "AWE Phone"},
    {"key": "sosmed",      "label": "Sosmed"},
    {"key": "peraturan",   "label": "Peraturan"},
    {"key": "voicebot",    "label": "Voicebot"},
    {"key": "umum",        "label": "Umum"},
]

# --- Katalog tautan menu. `path` HARUS sama persis dengan href di base.html ---
# (dipakai untuk pemetaan rute + filter sidebar server-side).
MENU_CATALOG = [
    {"key": "m_rag_chatbot",       "group": "rag_chatbot", "label": "RAG Chatbot",          "path": "/rag-chatbot"},
    {"key": "m_rag_eval_chatbot",  "group": "rag_chatbot", "label": "Evaluasi RAG Chatbot", "path": "/rag-eval-chatbot"},
    {"key": "m_rag_compare",       "group": "rag_chatbot", "label": "RAG vs LoRA",          "path": "/rag-vs-lora"},
    {"key": "m_handoff",           "group": "rag_chatbot", "label": "Handoff",              "path": "/handoff"},
    {"key": "m_rag_agent",         "group": "rag_agent",   "label": "RAG Agent",            "path": "/rag-agent"},
    {"key": "m_rag_eval",          "group": "rag_agent",   "label": "Evaluasi RAG Agent",   "path": "/rag-eval"},
    {"key": "m_dashboard",         "group": "dialogflow",  "label": "Dashboard",            "path": "/dashboard"},
    {"key": "m_deflection",        "group": "dialogflow",  "label": "Deflection",           "path": "/deflection"},
    {"key": "m_data",              "group": "dialogflow",  "label": "Data",                 "path": "/data"},
    {"key": "m_glossary",          "group": "dialogflow",  "label": "Glosarium",            "path": "/glossary"},
    {"key": "m_disambig",          "group": "dialogflow",  "label": "Disambiguasi",         "path": "/disambig"},
    {"key": "m_intentmap",         "group": "dialogflow",  "label": "Peta Intent",          "path": "/intentmap"},
    {"key": "m_lifecycle",         "group": "dialogflow",  "label": "Lifecycle",            "path": "/lifecycle"},
    {"key": "m_tools",             "group": "dialogflow",  "label": "Tools",                "path": "/tools"},
    {"key": "m_df_percakapan",     "group": "dialogflow",  "label": "Percakapan",           "path": "/dialogflow/percakapan"},
    {"key": "m_df_webhook",        "group": "dialogflow",  "label": "Webhook",              "path": "/df-webhook"},
    {"key": "m_awe_kelola",        "group": "awe_chat",    "label": "Kelola Data",          "path": "/awe/kelola"},
    {"key": "m_awe_dasbor",        "group": "awe_chat",    "label": "Dasbor",               "path": "/awe/dasbor"},
    {"key": "m_awe_coverage",      "group": "awe_chat",    "label": "Coverage",             "path": "/awe/coverage"},
    {"key": "m_awe_taksonomi",     "group": "awe_chat",    "label": "Taksonomi",            "path": "/awe/taksonomi"},
    {"key": "m_awe_sentimen",      "group": "awe_chat",    "label": "Sentimen",             "path": "/awe/sentimen"},
    {"key": "m_awe_percakapan",    "group": "awe_chat",    "label": "Percakapan",           "path": "/awe/percakapan"},
    {"key": "m_awe_pengguna",      "group": "awe_chat",    "label": "Pengguna Harian",      "path": "/awe/pengguna-harian"},
    {"key": "m_awe_penilaian",     "group": "awe_chat",    "label": "Penilaian QA",         "path": "/awe/penilaian"},
    {"key": "m_awe_telepon",       "group": "awe_phone",   "label": "Kelola Data Telepon",  "path": "/awe/telepon"},
    {"key": "m_awe_telepon_dash",  "group": "awe_phone",   "label": "Dashboard",            "path": "/awe/telepon/dashboard"},
    {"key": "m_awe_telepon_cov",   "group": "awe_phone",   "label": "Coverage",             "path": "/awe/telepon/coverage"},
    {"key": "m_awe_telepon_tax",   "group": "awe_phone",   "label": "Taksonomi",            "path": "/awe/telepon/taksonomi"},
    {"key": "m_awe_telepon_sen",   "group": "awe_phone",   "label": "Sentimen",             "path": "/awe/telepon/sentimen"},
    {"key": "m_awe_telepon_detail","group": "awe_phone",   "label": "Percakapan",           "path": "/awe/telepon/percakapan"},
    {"key": "m_awe_telepon_users", "group": "awe_phone",   "label": "Pengguna",             "path": "/awe/telepon/pengguna"},
    {"key": "m_sosmed_qna",        "group": "sosmed",      "label": "Sosmed QnA",           "path": "/sosmed"},
    {"key": "m_sosmed_monitor",    "group": "sosmed",      "label": "Monitor",              "path": "/sosmed/monitor"},
    {"key": "m_sosmed_kelola",     "group": "sosmed",      "label": "Kelola Data",          "path": "/sosmed/kelola"},
    {"key": "m_sosmed_sla",        "group": "sosmed",      "label": "SLA",                  "path": "/sosmed/sla"},
    {"key": "m_sosmed_deflection", "group": "sosmed",      "label": "Deflection",           "path": "/sosmed/deflection"},
    {"key": "m_peraturan",         "group": "peraturan",   "label": "Peraturan",            "path": "/peraturan"},
    {"key": "m_sop",               "group": "peraturan",   "label": "SOP",                  "path": "/sop"},
    {"key": "m_kamus",             "group": "peraturan",   "label": "Kamus",                "path": "/kamus"},
    {"key": "m_rag_harness",       "group": "peraturan",   "label": "RAG Harness",          "path": "/rag-harness"},
    {"key": "m_voicebot",          "group": "voicebot",    "label": "Voicebot",             "path": "/voicebot"},
    {"key": "m_voicebot_intents",  "group": "voicebot",    "label": "Intents",              "path": "/voicebot/intents"},
    {"key": "m_voicebot_lab",      "group": "voicebot",    "label": "Lab",                  "path": "/voicebot/lab"},
    {"key": "m_studio",            "group": "umum",        "label": "Studio",               "path": "/studio"},
    {"key": "m_laporan",           "group": "umum",        "label": "Laporan AI",           "path": "/laporan"},
    {"key": "m_users",             "group": "umum",        "label": "Pengguna & Peran",     "path": "/users"},
]

_BY_KEY = {m["key"]: m for m in MENU_CATALOG}
_BY_PATH = {m["path"]: m["key"] for m in MENU_CATALOG}
MENU_KEYS = [m["key"] for m in MENU_CATALOG]

# Menu yang SELALU tampil untuk user yang sudah login (beranda Studio/chat).
_BASELINE_MENUS = {"m_studio"}

# Peta {menu_key: area_coarse}. Diisi app_core lewat set_menu_areas() memakai
# _route_area() sebagai satu sumber kebenaran. Dipakai untuk fallback kompat.
_MENU_AREA = {}


def set_menu_areas(mapping):
    global _MENU_AREA
    if isinstance(mapping, dict):
        _MENU_AREA = {k: v for k, v in mapping.items() if k in _BY_KEY}


def menu_area(menu_key):
    return _MENU_AREA.get(menu_key)


def menu_key_for_path(path):
    return _BY_PATH.get(path)


def catalog():
    return {
        "groups": [dict(g) for g in MENU_GROUPS],
        "menus": [dict(m) for m in MENU_CATALOG],
    }


# --- Penyimpanan (tabel sendiri; users_db.connect dipakai ulang) --------------
def _init(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS role_menus ("
        " role_key TEXT NOT NULL,"
        " menu_key TEXT NOT NULL,"
        " PRIMARY KEY (role_key, menu_key))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_menu_grants ("
        " user_id INTEGER NOT NULL,"
        " menu_key TEXT NOT NULL,"
        " allow INTEGER NOT NULL DEFAULT 1,"
        " PRIMARY KEY (user_id, menu_key))"
    )
    conn.commit()


def _conn():
    c = usr.connect()
    _init(c)
    return c


_CACHE = {"loaded": False, "role_menus": {}, "user_menus": {}}


def _reset():
    _CACHE["loaded"] = False


def _load(conn=None):
    own = conn is None
    if own:
        conn = _conn()
    try:
        _init(conn)
        rm = {}
        for r in conn.execute("SELECT role_key, menu_key FROM role_menus").fetchall():
            rm.setdefault(r["role_key"], set()).add(r["menu_key"])
        um = {}
        for r in conn.execute("SELECT user_id, menu_key, allow FROM user_menu_grants").fetchall():
            um.setdefault(int(r["user_id"]), {})[r["menu_key"]] = int(r["allow"])
        _CACHE["role_menus"] = rm
        _CACHE["user_menus"] = um
        _CACHE["loaded"] = True
    finally:
        if own:
            conn.close()


def _snap():
    if not _CACHE.get("loaded"):
        try:
            _load()
        except Exception:
            pass
    return _CACHE


def role_configured(role):
    """True bila peran punya konfigurasi per-menu (mode fine aktif utk peran)."""
    return bool(_snap()["role_menus"].get(role or ""))


def menu_allowed(role, menu_key, user_id=None):
    mkey = menu_key or ""
    if mkey not in _BY_KEY:
        return False
    if mkey in _BASELINE_MENUS:
        return True
    snap = _snap()
    # 1) Override per-user (paling menentukan)
    if user_id is not None:
        try:
            uid = int(user_id)
        except Exception:
            uid = None
        if uid is not None:
            g = snap["user_menus"].get(uid)
            if g and mkey in g:
                return g[mkey] == 1
    # 2) Peran sudah dikonfigurasi per-menu
    rm = snap["role_menus"].get(role or "")
    if rm:
        return mkey in rm
    # 3) Fallback: izin area coarse lama (perilaku identik sebelum konfigurasi)
    area = _MENU_AREA.get(mkey)
    if area:
        try:
            return bool(usr.area_allowed(role, area, user_id=user_id))
        except Exception:
            return False
    return False


def menus_for(role, user_id=None):
    return [m["key"] for m in MENU_CATALOG if menu_allowed(role, m["key"], user_id)]


def groups_state(role, user_id=None):
    """{group_key: bool} = True bila minimal satu menu di grup boleh."""
    out = {g["key"]: False for g in MENU_GROUPS}
    for m in MENU_CATALOG:
        if menu_allowed(role, m["key"], user_id):
            out[m["group"]] = True
    return out


def menu_state(role, user_id=None):
    """{menu_key: bool} untuk seluruh katalog (dipakai template base.html)."""
    return {m["key"]: menu_allowed(role, m["key"], user_id) for m in MENU_CATALOG}


# --- API tulis (dipakai UI Kelola Akses per-menu) ----------------------------
def get_role_menus(role_key):
    """{'configured': bool, 'menus': [keys efektif]}. Bila belum dikonfigurasi,
    kembalikan menu efektif hasil fallback area (sebagai nilai awal centang)."""
    snap = _snap()
    rm = snap["role_menus"].get(role_key or "")
    return {
        "configured": bool(rm),
        "menus": sorted(rm) if rm else menus_for(role_key, None),
    }


def set_role_menus(role_key, menu_keys):
    keys = [k for k in (menu_keys or []) if k in _BY_KEY]
    if role_key == "admin":
        keys = list(MENU_KEYS)  # admin selalu penuh
    conn = _conn()
    try:
        conn.execute("DELETE FROM role_menus WHERE role_key=?", (role_key,))
        for k in keys:
            conn.execute(
                "INSERT OR IGNORE INTO role_menus (role_key,menu_key) VALUES (?,?)",
                (role_key, k),
            )
        conn.commit()
    finally:
        conn.close()
    _reset()
    return get_role_menus(role_key)


def clear_role_menus(role_key):
    """Hapus konfigurasi per-menu peran -> kembali ke mode area coarse lama."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM role_menus WHERE role_key=?", (role_key,))
        conn.commit()
    finally:
        conn.close()
    _reset()
    return {"ok": True}


def get_user_menus(user_id):
    return dict(_snap()["user_menus"].get(int(user_id), {}))


def set_user_menus(user_id, grants):
    norm = {}
    if isinstance(grants, dict):
        for k, v in grants.items():
            if k in _BY_KEY and v in (0, 1, "0", "1", True, False):
                norm[k] = 1 if v in (1, "1", True) else 0
    conn = _conn()
    try:
        conn.execute("DELETE FROM user_menu_grants WHERE user_id=?", (int(user_id),))
        for k, v in norm.items():
            conn.execute(
                "INSERT OR REPLACE INTO user_menu_grants (user_id,menu_key,allow) VALUES (?,?,?)",
                (int(user_id), k, int(v)),
            )
        conn.commit()
    finally:
        conn.close()
    _reset()
    return get_user_menus(user_id)
