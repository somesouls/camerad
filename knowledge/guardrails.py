# -*- coding: utf-8 -*-
"""guardrails.py — Fase 5: guardrail, keamanan & audit untuk jalur AGENTIC.

Melengkapi guardrail yang SUDAH ADA (read-only + allowlist di db.registry, PII
masking input di knowledge.agentic) dengan tiga lapis tambahan:

  1. AUDIT PERSISTEN — setiap job agentic (beserta tiap query & aksinya) dicatat
     ke tabel `agentic_audit` di reports.db. Teks pertanyaan/SQL/error di-MASK
     PII lebih dulu (common.pii_mask), dan catatan ini TIDAK ikut dibersihkan
     oleh TTL job (knowledge.jobs) sehingga jejaknya awet untuk tinjauan.
  2. RATE LIMIT per pengguna — batasi jumlah job agentic yang berjalan bersamaan
     dan jumlah job baru per menit, untuk mengendalikan biaya/beban.
  3. VERIFIKASI ALLOWLIST (ganda) — pengecekan bahwa database yang tersentuh
     tidak termasuk daftar dikecualikan (registry.EXCLUDED, mis. `users`).

SIFAT: ADITIF & NON-BREAKING.
- Modul baru; hanya dipanggil dari jalur job async (knowledge.jobs /
  knowledge.jobs_routes). Engine agentic (knowledge/agentic.py) & database sumber
  TIDAK diubah (tetap read-only lewat db.registry).
- Memakai file reports.db (env PIPELINE_REPORTS_DB_FILE) dengan TABEL TERPISAH
  `agentic_audit`; tidak menyentuh tabel lain.
- Semua operasi audit bersifat BEST-EFFORT: kegagalan audit tidak pernah
  menggagalkan permintaan pengguna.

Saklar via environment:
- AGENTIC_AUDIT=off       -> matikan pencatatan audit.
- AGENTIC_RATE_LIMIT=off  -> matikan rate limit.
- AGENTIC_MAX_CONCURRENT  -> maks job bersamaan per pengguna (default 2).
- AGENTIC_RATE_PER_MIN    -> maks job baru per 60 detik per pengguna (default 8).
"""
import os
import json
import sqlite3
import threading
import datetime as _dt

import db.reports_db as reports_db
import common.pii_mask as pii_mask

try:
    import db.registry as registry
except Exception:  # pragma: no cover - registry seharusnya selalu ada
    registry = None

# --- Konfigurasi (semua via env; default aman) ---------------------------
# Pakai file DB yang sama dengan Laporan & job (reports.db); tabel terpisah.
DB_FILE = reports_db.DB_FILE


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except Exception:
        return default


RATE_MAX_CONCURRENT = _int_env("AGENTIC_MAX_CONCURRENT", 2)  # queued+running / user
RATE_MAX_PER_MIN = _int_env("AGENTIC_RATE_PER_MIN", 8)       # job baru / 60d / user
AUDIT_MAX_SQL = _int_env("AGENTIC_AUDIT_MAX_SQL", 2000)      # potong SQL panjang

_INIT_LOCK = threading.Lock()
_INITED = False


def _now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def audit_enabled():
    v = (os.environ.get("AGENTIC_AUDIT", "on") or "").strip().lower()
    return v not in ("off", "0", "false", "no")


def rate_limit_enabled():
    v = (os.environ.get("AGENTIC_RATE_LIMIT", "on") or "").strip().lower()
    return v not in ("off", "0", "false", "no")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agentic_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            job_id TEXT,
            created_by TEXT,
            page TEXT,
            status TEXT,
            question TEXT,
            databases TEXT,
            steps TEXT,
            step_count INTEGER DEFAULT 0,
            query_count INTEGER DEFAULT 0,
            error TEXT,
            duration_ms INTEGER
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agentic_audit_ts "
                 "ON agentic_audit(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agentic_audit_user "
                 "ON agentic_audit(created_by)")
    conn.commit()
    return conn


def _ensure():
    global _INITED
    if _INITED:
        return
    with _INIT_LOCK:
        if _INITED:
            return
        conn = connect()
        try:
            init_db(conn)
        finally:
            conn.close()
        _INITED = True


# --- Allowlist (verifikasi ganda) ----------------------------------------
def excluded_dbs():
    """Kumpulan key database yang dikecualikan dari akses AI (mis. `users`)."""
    if registry is not None:
        try:
            return set(registry.EXCLUDED)
        except Exception:
            pass
    return {"users"}


def check_allowlist(databases):
    """Pastikan tak ada database dikecualikan yang tersentuh.

    Return (ok: bool, offending: list). Ini pengecekan GANDA; penegakan utama
    tetap di db.registry.run_select yang menolak database excluded sebelum
    koneksi dibuka.
    """
    ex = excluded_dbs()
    hit = sorted({d for d in (databases or []) if d in ex})
    return (len(hit) == 0, hit)


# --- Rate limit ----------------------------------------------------------
def _count(conn, sql, params):
    try:
        r = conn.execute(sql, params).fetchone()
        return int(r[0]) if r else 0
    except Exception:
        # Tabel agentic_jobs mungkin belum dibuat pada panggilan pertama.
        return 0


def check_rate_limit(created_by):
    """Cek batas laju per pengguna SEBELUM job dibuat.

    Return {"ok": True} atau {"ok": False, "error": str, "retry_after": int}.
    Membaca tabel agentic_jobs (file DB sama). Pengguna tanpa identitas tidak
    dibatasi (tak dapat dibedakan). Best-effort: kegagalan -> lolos.
    """
    if not rate_limit_enabled():
        return {"ok": True}
    user = (created_by or "").strip()
    if not user:
        return {"ok": True}
    try:
        conn = connect()
    except Exception:
        return {"ok": True}
    try:
        concurrent = _count(
            conn,
            "SELECT COUNT(*) FROM agentic_jobs WHERE created_by=? "
            "AND status IN ('queued','running')", (user,))
        if concurrent >= RATE_MAX_CONCURRENT:
            return {"ok": False, "retry_after": 10,
                    "error": ("Masih ada %d analisis agentic Anda yang berjalan. "
                              "Tunggu hingga selesai (maks %d bersamaan)."
                              % (concurrent, RATE_MAX_CONCURRENT))}
        cutoff = (_dt.datetime.now() - _dt.timedelta(seconds=60)
                  ).isoformat(timespec="seconds")
        recent = _count(
            conn,
            "SELECT COUNT(*) FROM agentic_jobs WHERE created_by=? "
            "AND created_at >= ?", (user, cutoff))
        if recent >= RATE_MAX_PER_MIN:
            return {"ok": False, "retry_after": 60,
                    "error": ("Terlalu banyak permintaan analisis agentic "
                              "(maks %d per menit). Coba lagi sebentar."
                              % RATE_MAX_PER_MIN)}
        return {"ok": True}
    finally:
        conn.close()


# --- Audit persisten -----------------------------------------------------
def _mask(s):
    try:
        return pii_mask.mask_text(s or "")
    except Exception:
        return s or ""


def record(job_id="", created_by="", page="", status="", question="",
           databases=None, steps=None, error=None, duration_ms=None):
    """Catat satu baris audit persisten (PII di-mask). Best-effort; tak melempar.

    `steps` adalah jejak dari engine agentic (tiap query/aksi: type, db, ok,
    rows, sql, error). SQL & error ikut di-mask + dipotong AUDIT_MAX_SQL char.
    """
    if not audit_enabled():
        return
    try:
        _ensure()
    except Exception:
        return
    try:
        databases = databases or []
        steps = steps or []
        safe_steps = []
        qcount = 0
        for st in steps:
            if not isinstance(st, dict):
                continue
            typ = st.get("type")
            if typ == "query":
                qcount += 1
            sql = st.get("sql")
            if sql:
                sql = _mask(str(sql))[:AUDIT_MAX_SQL]
            err = st.get("error")
            safe_steps.append({
                "type": typ,
                "db": st.get("db"),
                "ok": st.get("ok"),
                "rows": st.get("rows"),
                "sql": sql,
                "error": (_mask(str(err)) if err else None),
            })
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO agentic_audit (ts, job_id, created_by, page, "
                "status, question, databases, steps, step_count, query_count, "
                "error, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), job_id or "", created_by or "", page or "",
                 status or "", _mask(question)[:2000],
                 json.dumps(databases, ensure_ascii=False),
                 json.dumps(safe_steps, ensure_ascii=False),
                 len(safe_steps), qcount,
                 (_mask(str(error)) if error else None), duration_ms),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def list_audit(limit=100, created_by=None):
    """Baca audit terbaru (untuk tinjauan/administrasi). Return list of dict."""
    _ensure()
    limit = max(1, min(int(limit or 100), 1000))
    conn = connect()
    try:
        if created_by:
            rows = conn.execute(
                "SELECT * FROM agentic_audit WHERE created_by=? "
                "ORDER BY id DESC LIMIT ?", (created_by, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agentic_audit ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    # Smoke test offline-safe: file DB sementara, tanpa LLM/DB sumber.
    import tempfile

    DB_FILE = os.path.join(tempfile.gettempdir(), "agentic_audit_smoke.db")
    try:
        os.remove(DB_FILE)
    except Exception:
        pass
    _INITED = False

    # 1) Allowlist ganda.
    ok, hit = check_allowlist(["analytics", "users"])
    assert (not ok) and ("users" in hit), "allowlist harus menolak users"
    ok2, _hit2 = check_allowlist(["analytics", "sosmed"])
    assert ok2, "allowlist harus lolos untuk database biasa"

    # 2) Audit + PII masking (pertanyaan & SQL).
    record(job_id="J1", created_by="u1", page="dashboard", status="done",
           question="NIK saya 1234567890123456 email a@b.com",
           databases=["analytics"],
           steps=[{"type": "query", "db": "analytics", "ok": True, "rows": 3,
                   "sql": "SELECT * FROM interactions WHERE hp='08123456789'"}],
           duration_ms=1200)
    rows = list_audit(limit=10)
    assert rows and rows[0]["job_id"] == "J1", "audit tidak tersimpan"
    assert "<NIK>" in rows[0]["question"], "NIK pertanyaan harus di-mask"
    assert "1234567890123456" not in rows[0]["question"], "NIK bocor"
    assert rows[0]["query_count"] == 1, "query_count salah"
    assert "08123456789" not in rows[0]["steps"], "HP di SQL harus di-mask"

    # 3) Rate limit aman saat tabel agentic_jobs belum ada -> lolos.
    rl = check_rate_limit("u_none")
    assert rl.get("ok"), "rate limit harus lolos saat tak ada job"

    print("AGENTIC_GUARDRAILS_SMOKE_OK")
