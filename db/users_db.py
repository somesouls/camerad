# users_db.py — Autentikasi, peran, dan sesi untuk Pipeline Lokal DJP
# Stdlib-only. Sandi disimpan sebagai PBKDF2-HMAC-SHA256 + salt per-user.
import os
import re
import time
import json
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # paket db/ -> root repo

PBKDF2_ITERATIONS = 200000

_CAP = {
    "admin": {"read", "edit", "approve", "ingest", "admin", "assess"},
    "analis": {"read", "edit", "approve", "ingest"},
    "assessor": {"read", "assess"},
    "viewer": {"read"},
    "agent": {"read"},
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _norm_username(u):
    return (u or "").strip().lower()


def connect(db_path=None):
    path = db_path or os.environ.get("PIPELINE_USERS_DB_FILE") or os.path.join(_BASE_DIR, "users.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " username TEXT UNIQUE NOT NULL,"
        " nama TEXT DEFAULT '',"
        " pass_hash TEXT NOT NULL,"
        " pass_salt TEXT NOT NULL,"
        " iterations INTEGER NOT NULL,"
        " role TEXT NOT NULL DEFAULT 'viewer',"
        " aktif INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT, updated_at TEXT, last_login TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " token TEXT PRIMARY KEY,"
        " user_id INTEGER NOT NULL,"
        " created_at TEXT, expires_at REAL, last_seen TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sess_user ON sessions(user_id)")
    # Migrasi: kolom avatar (data URL) untuk foto profil
    try:
        _cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "avatar" not in _cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
    except Exception:
        pass
    # --- RBAC dinamis (ADITIF, non-breaking): peran + area per-peran + grant per-user ---
    conn.execute(
        "CREATE TABLE IF NOT EXISTS roles ("
        " key TEXT PRIMARY KEY,"
        " label TEXT DEFAULT '',"
        " level INTEGER NOT NULL DEFAULT 4,"
        " caps TEXT NOT NULL DEFAULT 'read',"
        " is_system INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS role_areas ("
        " role_key TEXT NOT NULL,"
        " area TEXT NOT NULL,"
        " PRIMARY KEY (role_key, area))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_area_grants ("
        " user_id INTEGER NOT NULL,"
        " area TEXT NOT NULL,"
        " allow INTEGER NOT NULL DEFAULT 1,"
        " PRIMARY KEY (user_id, area))"
    )
    conn.commit()
    _seed_rbac(conn)
    return conn


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    )
    return dk.hex(), salt, int(iterations)


def verify_password(password, pass_hash, salt, iterations):
    try:
        calc, _, _ = hash_password(password, salt, int(iterations))
    except Exception:
        return False
    return secrets.compare_digest(calc, pass_hash or "")


def _pub(row):
    if row is None:
        return None
    d = dict(row)
    for k in ("pass_hash", "pass_salt", "iterations"):
        d.pop(k, None)
    return d


def get_user(conn, username):
    return conn.execute(
        "SELECT * FROM users WHERE username=?",
        (_norm_username(username),),
    ).fetchone()


def get_user_by_id(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()


def list_users(conn):
    rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return [_pub(r) for r in rows]


def _count_active_admins(conn, exclude_id=None):
    if exclude_id is None:
        r = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND aktif=1").fetchone()
    else:
        r = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND aktif=1 AND id!=?",
            (int(exclude_id),),
        ).fetchone()
    return int(r["c"] if r else 0)


def _valid_role(conn, role):
    """Peran sah jika built-in (_CAP) ATAU terdaftar di tabel roles (dinamis)."""
    if role in _CAP:
        return True
    try:
        return conn.execute("SELECT 1 FROM roles WHERE key=?", (role,)).fetchone() is not None
    except Exception:
        return False


def create_user(conn, username, password, nama="", role="viewer"):
    u = _norm_username(username)
    if not u:
        return {"ok": False, "error": "Username wajib diisi."}
    if not password or len(password) < 6:
        return {"ok": False, "error": "Sandi minimal 6 karakter."}
    if not _valid_role(conn, role):
        role = "viewer"
    if get_user(conn, u):
        return {"ok": False, "error": "Username sudah dipakai."}
    h, salt, it = hash_password(password)
    now = _now()
    conn.execute(
        "INSERT INTO users (username,nama,pass_hash,pass_salt,iterations,role,aktif,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,1,?,?)",
        (u, nama or "", h, salt, it, role, now, now),
    )
    conn.commit()
    return {"ok": True, "user": _pub(get_user(conn, u))}


def update_user(conn, uid, nama=None, role=None, aktif=None):
    row = get_user_by_id(conn, uid)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    new_role = row["role"] if role is None else role
    if not _valid_role(conn, new_role):
        return {"ok": False, "error": "Peran tidak valid."}
    new_aktif = row["aktif"] if aktif is None else (1 if aktif else 0)
    if row["role"] == "admin" and row["aktif"] == 1:
        if (new_role != "admin" or new_aktif == 0) and _count_active_admins(conn, exclude_id=row["id"]) == 0:
            return {"ok": False, "error": "Tidak bisa menonaktifkan/menurunkan admin aktif terakhir."}
    conn.execute(
        "UPDATE users SET nama=?, role=?, aktif=?, updated_at=? WHERE id=?",
        (row["nama"] if nama is None else nama, new_role, new_aktif, _now(), row["id"]),
    )
    conn.commit()
    return {"ok": True, "user": _pub(get_user_by_id(conn, uid))}


def set_password(conn, uid, new_password):
    row = get_user_by_id(conn, uid)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    if not new_password or len(new_password) < 6:
        return {"ok": False, "error": "Sandi minimal 6 karakter."}
    h, salt, it = hash_password(new_password)
    conn.execute(
        "UPDATE users SET pass_hash=?, pass_salt=?, iterations=?, updated_at=? WHERE id=?",
        (h, salt, it, _now(), row["id"]),
    )
    conn.commit()
    return {"ok": True}


def change_own_password(conn, uid, old_password, new_password):
    """Ganti sandi mandiri: wajib verifikasi sandi lama dulu."""
    row = get_user_by_id(conn, uid)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    if not verify_password(old_password, row["pass_hash"], row["pass_salt"], row["iterations"]):
        return {"ok": False, "error": "Sandi lama salah."}
    if not new_password or len(new_password) < 6:
        return {"ok": False, "error": "Sandi baru minimal 6 karakter."}
    h, salt, it = hash_password(new_password)
    conn.execute(
        "UPDATE users SET pass_hash=?, pass_salt=?, iterations=?, updated_at=? WHERE id=?",
        (h, salt, it, _now(), row["id"]),
    )
    conn.commit()
    return {"ok": True}


def set_avatar(conn, uid, avatar):
    """Simpan avatar sebagai data URL (image/*). String kosong = hapus foto."""
    row = get_user_by_id(conn, uid)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    av = avatar or ""
    if av and not av.startswith("data:image/"):
        return {"ok": False, "error": "Format gambar tidak valid."}
    if len(av) > 1_500_000:
        return {"ok": False, "error": "Ukuran gambar terlalu besar (maks ~1 MB)."}
    conn.execute("UPDATE users SET avatar=?, updated_at=? WHERE id=?", (av, _now(), row["id"]))
    conn.commit()
    return {"ok": True, "avatar": av}


def delete_user(conn, uid):
    row = get_user_by_id(conn, uid)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    if row["role"] == "admin" and row["aktif"] == 1 and _count_active_admins(conn, exclude_id=row["id"]) == 0:
        return {"ok": False, "error": "Tidak bisa menghapus admin aktif terakhir."}
    conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    conn.execute("DELETE FROM user_area_grants WHERE user_id=?", (row["id"],))
    conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
    conn.commit()
    return {"ok": True}


def authenticate(conn, username, password):
    row = get_user(conn, username)
    if not row or not row["aktif"]:
        return None
    if not verify_password(password, row["pass_hash"], row["pass_salt"], row["iterations"]):
        return None
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), row["id"]))
    conn.commit()
    return _pub(row)


def session_ttl():
    try:
        hrs = float(os.environ.get("PIPELINE_SESSION_TTL_HOURS", "12"))
    except Exception:
        hrs = 12.0
    return int(hrs * 3600)


def create_session(conn, user_id, ttl=None):
    token = secrets.token_urlsafe(32)
    ttl = ttl or session_ttl()
    conn.execute(
        "INSERT INTO sessions (token,user_id,created_at,expires_at,last_seen) VALUES (?,?,?,?,?)",
        (token, int(user_id), _now(), time.time() + ttl, _now()),
    )
    conn.commit()
    return token


def get_session_user(conn, token):
    if not token:
        return None
    r = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if not r:
        return None
    if float(r["expires_at"] or 0) < time.time():
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    conn.execute("UPDATE sessions SET last_seen=? WHERE token=?", (_now(), token))
    conn.commit()
    u = get_user_by_id(conn, r["user_id"])
    if not u or not u["aktif"]:
        return None
    return _pub(u)


def delete_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


def purge_expired(conn):
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    conn.commit()


def seed_admin(conn):
    init_db(conn)
    r = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
    if int(r["c"] if r else 0) > 0:
        return None
    u = _norm_username(os.environ.get("PIPELINE_ADMIN_USER", "admin"))
    pw = os.environ.get("PIPELINE_ADMIN_PASSWORD", "admin123")
    res = create_user(conn, u, pw, nama="Administrator", role="admin")
    return {"username": u, "default_password": pw} if res.get("ok") else None


# =====================================================================
# Fondasi RBAC: label peran + segregasi per-area
# =====================================================================
ROLE_LABEL = {
    "admin": "Administrator",
    "analis": "Analis",
    "assessor": "Assessor QA",
    "viewer": "Peninjau",
    "agent": "Agent Kring Pajak",
}

# Area akses HARDCODED (fallback bila DB RBAC kosong/tak terbaca). Ini menjaga
# perilaku lama tetap identik meski lapisan DB gagal dimuat.
_AREA_ROLES = {
    "dialogflow": {"admin", "analis", "viewer"},
    "awe":        {"admin", "analis", "assessor", "viewer"},
    "awe_manage": {"admin", "analis"},
    "assess":     {"admin", "assessor"},
    "common":     {"admin", "analis", "assessor", "viewer"},
    "chat":       {"admin", "analis", "assessor", "viewer", "agent"},
    "account":    {"admin", "analis", "assessor", "viewer", "agent"},
    "users":      {"admin"},
    "peraturan":  {"admin"},
    # Area hasil pemecahan can_peraturan (aditif). Default = {admin} SAJA, sama
    # seperti 'peraturan' lama, sehingga perilaku peran lama tidak berubah.
    "rag_config": {"admin"},
    "df_webhook": {"admin"},
    "voicebot":   {"admin"},
}

# Area "baseline" yang SELALU aktif untuk setiap peran yang sudah login
# (beranda chat + halaman Profil). Dulu semua peran memilikinya, jadi menjadikan
# ini selalu-true tidak mengubah perilaku, sekaligus mencegah user terkunci
# (redirect loop) saat peran baru belum diberi menu apa pun.
_BASELINE_AREAS = {"account", "chat"}

# Katalog area yang bisa dicentang admin (mengikuti accordion sidebar base.html).
# Ini sumber kebenaran untuk seeding admin & untuk UI Kelola Akses.
AREA_CATALOG = [
    {"key": "rag_config", "label": "RAG Chatbot & Agent"},
    {"key": "dialogflow", "label": "Dialogflow"},
    {"key": "df_webhook", "label": "Webhook Chatbot (Dialogflow ES)"},
    {"key": "awe",        "label": "AWE Chat — dasbor & analitik"},
    {"key": "awe_manage", "label": "Kelola Data (AWE Chat / AWE Phone / Sosmed)"},
    {"key": "assess",     "label": "Penilaian QA (AWE)"},
    {"key": "common",     "label": "Sosmed & Laporan AI"},
    {"key": "peraturan",  "label": "Peraturan · SOP · Kamus · RAG Harness"},
    {"key": "voicebot",   "label": "Voicebot"},
    {"key": "users",      "label": "Pengguna & Peran (khusus admin)"},
]

CAP_CATALOG = ["read", "edit", "approve", "ingest", "assess", "admin"]

# Level jenjang default: admin=0 ... agent=5 (metadata jenjang, bukan penentu
# akses). Akses tetap ditentukan area per-peran + grant per-user.
_DEFAULT_LEVELS = {"admin": 0, "analis": 4, "assessor": 4, "viewer": 4, "agent": 5}

# Peran jenjang baru (design). Area KOSONG saat seed -> admin yang menentukan.
_DESIGN_ROLES = [
    ("kepala_kantor",         "Kepala Kantor",          1),
    ("kepala_seksi",          "Kepala Seksi",           2),
    ("spv_operasional",       "SPV Operasional",        3),
    ("spv_analis",            "SPV Analis",             3),
    ("spv_assessor",          "SPV Assessor",           3),
    ("timleader_operasional", "Tim Leader Operasional", 4),
    ("analis_chatbot",        "Analis Chatbot",         4),
    ("analis_umum",           "Analis Umum",            4),
    ("agent_operasional",     "Agent Operasional",      5),
]

_RBAC_SEEDED = False
_RBAC_CACHE = {"loaded": False, "roles": {}, "role_areas": {}, "user_grants": {}}


def _catalog_area_keys():
    return [a["key"] for a in AREA_CATALOG]


def _parse_caps(s):
    if not s:
        return set()
    if isinstance(s, (set, list, tuple)):
        return set(x for x in s if x)
    return set(x.strip() for x in str(s).split(",") if x.strip())


def _norm_key(k):
    k = (k or "").strip().lower()
    k = re.sub(r"[^a-z0-9_]+", "_", k)
    return k.strip("_")


def _rbac_reset():
    _RBAC_CACHE["loaded"] = False


def _load_rbac_cache(conn=None):
    own = conn is None
    if own:
        conn = connect()
    try:
        roles = {}
        for r in conn.execute("SELECT * FROM roles").fetchall():
            roles[r["key"]] = dict(r)
        ra = {}
        for r in conn.execute("SELECT role_key, area FROM role_areas").fetchall():
            ra.setdefault(r["role_key"], set()).add(r["area"])
        ug = {}
        for r in conn.execute("SELECT user_id, area, allow FROM user_area_grants").fetchall():
            ug.setdefault(int(r["user_id"]), {})[r["area"]] = int(r["allow"])
        _RBAC_CACHE["roles"] = roles
        _RBAC_CACHE["role_areas"] = ra
        _RBAC_CACHE["user_grants"] = ug
        _RBAC_CACHE["loaded"] = True
    finally:
        if own:
            conn.close()


def _snapshot():
    if not _RBAC_CACHE.get("loaded"):
        try:
            _load_rbac_cache()
        except Exception:
            pass
    return _RBAC_CACHE


def _seed_rbac(conn):
    """Isi tabel roles/role_areas sekali (idempoten) mencerminkan PERSIS perilaku
    hardcoded, lalu tambahkan peran jenjang baru dengan area kosong."""
    global _RBAC_SEEDED
    if _RBAC_SEEDED:
        return
    try:
        row = conn.execute("SELECT COUNT(*) c FROM roles").fetchone()
        if int(row["c"] if row else 0) == 0:
            now = _now()
            for key in ("admin", "analis", "assessor", "viewer", "agent"):
                caps = ",".join(sorted(_CAP.get(key, {"read"})))
                lvl = _DEFAULT_LEVELS.get(key, 4)
                conn.execute(
                    "INSERT OR IGNORE INTO roles (key,label,level,caps,is_system,created_at,updated_at)"
                    " VALUES (?,?,?,?,1,?,?)",
                    (key, ROLE_LABEL.get(key, key), lvl, caps, now, now),
                )
                for area, roles in _AREA_ROLES.items():
                    if area in _BASELINE_AREAS:
                        continue
                    if key in roles:
                        conn.execute(
                            "INSERT OR IGNORE INTO role_areas (role_key,area) VALUES (?,?)",
                            (key, area),
                        )
            # admin: pastikan memegang SEMUA area yang bisa dicentang.
            for a in _catalog_area_keys():
                conn.execute(
                    "INSERT OR IGNORE INTO role_areas (role_key,area) VALUES (?,?)",
                    ("admin", a),
                )
            # Peran jenjang baru: area kosong (admin yang menentukan), caps 'read'.
            for key, label, lvl in _DESIGN_ROLES:
                conn.execute(
                    "INSERT OR IGNORE INTO roles (key,label,level,caps,is_system,created_at,updated_at)"
                    " VALUES (?,?,?,?,0,?,?)",
                    (key, label, lvl, "read", now, now),
                )
            conn.commit()
    except Exception:
        pass
    _RBAC_SEEDED = True
    _rbac_reset()


# --- API baca (dipakai middleware & template) -------------------------------
def role_label(role):
    snap = _snapshot()
    r = snap["roles"].get(role or "") if snap.get("loaded") else None
    if r and r.get("label"):
        return r["label"]
    return ROLE_LABEL.get(role or "", (role or "-"))


def role_level(role):
    snap = _snapshot()
    r = snap["roles"].get(role or "") if snap.get("loaded") else None
    if r is not None:
        try:
            return int(r.get("level"))
        except Exception:
            return 4
    return _DEFAULT_LEVELS.get(role or "", 4)


def can(role, action):
    snap = _snapshot()
    r = snap["roles"].get(role or "") if snap.get("loaded") else None
    if r is not None:
        return (action or "") in _parse_caps(r.get("caps"))
    return (action or "") in _CAP.get(role or "", set())


def area_allowed(role, area, user_id=None):
    area = area or ""
    if area in _BASELINE_AREAS:
        return True
    snap = _snapshot()
    if user_id is not None:
        try:
            uid = int(user_id)
        except Exception:
            uid = None
        if uid is not None:
            g = snap["user_grants"].get(uid)
            if g and area in g:
                return g[area] == 1
    if snap.get("loaded") and (role or "") in snap["roles"]:
        return area in snap["role_areas"].get(role or "", set())
    return (role or "") in _AREA_ROLES.get(area, set())


# --- API tulis (dipakai CRUD Kelola Akses) ----------------------------------
def list_roles(conn):
    out = []
    for r in conn.execute("SELECT * FROM roles ORDER BY level, key").fetchall():
        d = dict(r)
        d["caps"] = sorted(_parse_caps(d.get("caps")))
        d["areas"] = sorted(
            x["area"] for x in conn.execute(
                "SELECT area FROM role_areas WHERE role_key=?", (d["key"],)
            ).fetchall()
        )
        cnt = conn.execute("SELECT COUNT(*) c FROM users WHERE role=?", (d["key"],)).fetchone()
        d["user_count"] = int(cnt["c"] if cnt else 0)
        out.append(d)
    return out


def get_role(conn, key):
    r = conn.execute("SELECT * FROM roles WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["caps"] = sorted(_parse_caps(d.get("caps")))
    d["areas"] = sorted(
        x["area"] for x in conn.execute(
            "SELECT area FROM role_areas WHERE role_key=?", (key,)
        ).fetchall()
    )
    return d


def _set_role_areas(conn, key, areas):
    valid = set(_catalog_area_keys())
    areas = [a for a in (areas or []) if a in valid]
    conn.execute("DELETE FROM role_areas WHERE role_key=?", (key,))
    for a in areas:
        conn.execute("INSERT OR IGNORE INTO role_areas (role_key,area) VALUES (?,?)", (key, a))


def save_role(conn, key=None, label="", level=4, caps=None, areas=None, orig_key=None):
    """Buat atau perbarui peran. orig_key diisi saat edit."""
    caps = sorted(c for c in _parse_caps(caps) if c in CAP_CATALOG) or ["read"]
    try:
        level = int(level)
    except Exception:
        level = 4
    editing = bool(orig_key)
    if editing:
        row = conn.execute("SELECT * FROM roles WHERE key=?", (orig_key,)).fetchone()
        if not row:
            return {"ok": False, "error": "Peran tidak ditemukan."}
        # Lindungi peran 'admin': harus tetap punya cap 'admin' + area 'users'.
        if orig_key == "admin":
            caps = sorted(set(caps) | {"admin", "read"})
            areas = sorted(set(areas or []) | {"users"})
            label = label or row["label"]
        conn.execute(
            "UPDATE roles SET label=?, level=?, caps=?, updated_at=? WHERE key=?",
            (label or row["label"], level, ",".join(caps), _now(), orig_key),
        )
        _set_role_areas(conn, orig_key, areas)
        conn.commit()
        _rbac_reset()
        return {"ok": True, "role": get_role(conn, orig_key)}
    # create
    nk = _norm_key(key)
    if not nk:
        return {"ok": False, "error": "Kunci peran wajib diisi (huruf/angka)."}
    if conn.execute("SELECT 1 FROM roles WHERE key=?", (nk,)).fetchone():
        return {"ok": False, "error": "Kunci peran sudah dipakai."}
    now = _now()
    conn.execute(
        "INSERT INTO roles (key,label,level,caps,is_system,created_at,updated_at) VALUES (?,?,?,?,0,?,?)",
        (nk, label or nk, level, ",".join(caps), now, now),
    )
    _set_role_areas(conn, nk, areas)
    conn.commit()
    _rbac_reset()
    return {"ok": True, "role": get_role(conn, nk)}


def delete_role(conn, key):
    row = conn.execute("SELECT * FROM roles WHERE key=?", (key,)).fetchone()
    if not row:
        return {"ok": False, "error": "Peran tidak ditemukan."}
    if int(row["is_system"] or 0) == 1:
        return {"ok": False, "error": "Peran bawaan sistem tidak bisa dihapus."}
    cnt = conn.execute("SELECT COUNT(*) c FROM users WHERE role=?", (key,)).fetchone()
    if int(cnt["c"] if cnt else 0) > 0:
        return {"ok": False, "error": "Masih ada pengguna memakai peran ini. Pindahkan dulu."}
    conn.execute("DELETE FROM role_areas WHERE role_key=?", (key,))
    conn.execute("DELETE FROM roles WHERE key=?", (key,))
    conn.commit()
    _rbac_reset()
    return {"ok": True}

def get_user_grants(conn, user_id):
    """Kembalikan {area: 1|0} override khusus user (di atas peran)."""
    out = {}
    for r in conn.execute(
        "SELECT area, allow FROM user_area_grants WHERE user_id=?", (int(user_id),)
    ).fetchall():
        out[r["area"]] = int(r["allow"])
    return out


def set_user_grants(conn, user_id, grants):
    """Simpan override akses per-user. `grants` = {area: 1|0} atau
    {"allow": [area,...], "revoke": [area,...]}. Area di luar katalog diabaikan.
    Nilai selain 0/1 pada area dianggap 'ikuti peran' (baris dihapus)."""
    row = get_user_by_id(conn, user_id)
    if not row:
        return {"ok": False, "error": "User tidak ditemukan."}
    valid = set(_catalog_area_keys())
    norm = {}
    if isinstance(grants, dict) and ("allow" in grants or "revoke" in grants):
        for a in (grants.get("allow") or []):
            if a in valid:
                norm[a] = 1
        for a in (grants.get("revoke") or []):
            if a in valid:
                norm[a] = 0
    elif isinstance(grants, dict):
        for a, v in grants.items():
            if a not in valid:
                continue
            if v in (0, 1, "0", "1", True, False):
                norm[a] = 1 if v in (1, "1", True) else 0
    conn.execute("DELETE FROM user_area_grants WHERE user_id=?", (int(user_id),))
    for a, v in norm.items():
        conn.execute(
            "INSERT OR REPLACE INTO user_area_grants (user_id,area,allow) VALUES (?,?,?)",
            (int(user_id), a, int(v)),
        )
    conn.commit()
    _rbac_reset()
    return {"ok": True, "grants": get_user_grants(conn, user_id)}


def access_catalog():
    """Metadata untuk UI Kelola Akses (area yang bisa dicentang + daftar cap)."""
    return {"areas": [dict(a) for a in AREA_CATALOG], "caps": list(CAP_CATALOG)}
