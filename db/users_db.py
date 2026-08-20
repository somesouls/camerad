# users_db.py — Autentikasi, peran, dan sesi untuk Pipeline Lokal DJP
# Stdlib-only. Sandi disimpan sebagai PBKDF2-HMAC-SHA256 + salt per-user.
import os
import time
import sqlite3
import hashlib
import secrets
from datetime import datetime, timezone

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
    path = db_path or os.environ.get("PIPELINE_USERS_DB_FILE", "users.db")
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
    conn.commit()
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


def create_user(conn, username, password, nama="", role="viewer"):
    u = _norm_username(username)
    if not u:
        return {"ok": False, "error": "Username wajib diisi."}
    if not password or len(password) < 6:
        return {"ok": False, "error": "Sandi minimal 6 karakter."}
    if role not in _CAP:
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
    if new_role not in _CAP:
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


def can(role, action):
    return action in _CAP.get(role or "", set())


def seed_admin(conn):
    init_db(conn)
    r = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
    if int(r["c"] if r else 0) > 0:
        return None
    u = _norm_username(os.environ.get("PIPELINE_ADMIN_USER", "admin"))
    pw = os.environ.get("PIPELINE_ADMIN_PASSWORD", "admin123")
    res = create_user(conn, u, pw, nama="Administrator", role="admin")
    return {"username": u, "default_password": pw} if res.get("ok") else None


# --- Fondasi RBAC: label peran + segregasi per-area (ditambahkan) ---
ROLE_LABEL = {
    "admin": "Administrator",
    "analis": "Analis",
    "assessor": "Assessor QA",
    "viewer": "Peninjau",
    "agent": "Agent Kring Pajak",
}

# Area akses (di atas kapabilitas). Dipakai middleware & template menu.
_AREA_ROLES = {
    "dialogflow": {"admin", "analis", "viewer"},
    "awe":        {"admin", "analis", "assessor", "viewer"},
    "awe_manage": {"admin", "analis"},
    "assess":     {"admin", "assessor"},
    "common":     {"admin", "analis", "assessor", "viewer"},
    # Kanal chat RAG Agent Kring Pajak + Studio Dokumen: semua peran termasuk 'agent'.
    "chat":       {"admin", "analis", "assessor", "viewer", "agent"},
    "account":    {"admin", "analis", "assessor", "viewer", "agent"},
    "users":      {"admin"},
    "peraturan":  {"admin"},
}


def role_label(role):
    return ROLE_LABEL.get(role or "", (role or "-"))


def area_allowed(role, area):
    return (role or "") in _AREA_ROLES.get(area or "", set())
