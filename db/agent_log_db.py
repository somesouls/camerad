# -*- coding: utf-8 -*-
"""
agent_log_db.py — Log chat RAG Agent Kring Pajak + feedback + kuota harian.

Stdlib-only (sqlite3, WAL). Menyimpan SETIAP interaksi chat RAG halaman utama
(pertanyaan + jawaban + sumber + waktu + agent + feedback jempol) agar
keandalan LLM bisa direview, serta mengelola kuota harian (batas pertanyaan /
token) per target:
  - 'agent'   : chat profil Agent Kring Pajak (login peran agent)
  - 'chatbot' : ChatBot Dialogflow (Wajib Pajak)

DB file: env PIPELINE_AGENT_LOG_DB_FILE (default agent_log.db).
"""
import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta

DEFAULT_MAKS_TANYA = 100
_TARGETS = ("agent", "chatbot")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today_range_utc():
    """Rentang [mulai, selesai) UTC untuk hari kalender Asia/Jakarta (UTC+7)."""
    now = datetime.now(timezone.utc)
    jkt = now + timedelta(hours=7)
    start_jkt = jkt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_jkt - timedelta(hours=7)
    end_utc = start_utc + timedelta(days=1)
    return (start_utc.strftime("%Y-%m-%d %H:%M:%S"),
            end_utc.strftime("%Y-%m-%d %H:%M:%S"))


def connect(db_path=None):
    path = db_path or os.environ.get("PIPELINE_AGENT_LOG_DB_FILE", "agent_log.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rag_chat_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT,"
        " username TEXT DEFAULT '',"
        " role TEXT DEFAULT '',"
        " profil TEXT DEFAULT '',"
        " question TEXT DEFAULT '',"
        " answer TEXT DEFAULT '',"
        " sources_json TEXT DEFAULT '[]',"
        " grounded INTEGER DEFAULT 0,"
        " domain TEXT DEFAULT '',"
        " feedback TEXT DEFAULT '',"
        " feedback_at TEXT DEFAULT '')"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_log_user_ts ON rag_chat_log(username, ts)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rag_quota ("
        " target TEXT PRIMARY KEY,"
        " maks_tanya INTEGER DEFAULT 100,"
        " maks_token INTEGER DEFAULT 0,"
        " updated_at TEXT,"
        " updated_by TEXT DEFAULT '')"
    )
    conn.commit()
    for tgt in _TARGETS:
        row = conn.execute("SELECT target FROM rag_quota WHERE target=?", (tgt,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO rag_quota (target, maks_tanya, maks_token, updated_at, updated_by)"
                " VALUES (?,?,?,?,?)",
                (tgt, DEFAULT_MAKS_TANYA, 0, _now(), "sistem"),
            )
    conn.commit()
    return conn


def _c():
    return init_db(connect())


def log_chat(username, role, profil, question, answer, sources, grounded, domain):
    """Catat satu interaksi. Kembalikan log_id (int) atau None bila gagal."""
    try:
        c = _c()
        try:
            cur = c.execute(
                "INSERT INTO rag_chat_log (ts, username, role, profil, question, answer,"
                " sources_json, grounded, domain, feedback, feedback_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), username or "", role or "", profil or "",
                 question or "", answer or "",
                 json.dumps(sources or [], ensure_ascii=False),
                 1 if grounded else 0, domain or "", "", ""),
            )
            c.commit()
            return int(cur.lastrowid)
        finally:
            c.close()
    except Exception:
        return None


def set_feedback(log_id, rating, username=""):
    rating = (rating or "").strip().lower()
    if rating not in ("up", "down"):
        return {"ok": False, "error": "Nilai feedback harus 'up' atau 'down'."}
    try:
        c = _c()
        try:
            row = c.execute("SELECT id FROM rag_chat_log WHERE id=?", (int(log_id),)).fetchone()
            if not row:
                return {"ok": False, "error": "Log tidak ditemukan."}
            c.execute(
                "UPDATE rag_chat_log SET feedback=?, feedback_at=? WHERE id=?",
                (rating, _now(), int(log_id)),
            )
            c.commit()
            return {"ok": True, "feedback": rating}
        finally:
            c.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def count_today(username):
    if not username:
        return 0
    lo, hi = _today_range_utc()
    try:
        c = _c()
        try:
            r = c.execute(
                "SELECT COUNT(*) n FROM rag_chat_log WHERE username=? AND ts>=? AND ts<?",
                (username, lo, hi),
            ).fetchone()
            return int(r["n"] if r else 0)
        finally:
            c.close()
    except Exception:
        return 0


def get_quota(target):
    target = (target or "").strip().lower()
    try:
        c = _c()
        try:
            r = c.execute("SELECT * FROM rag_quota WHERE target=?", (target,)).fetchone()
            if r:
                return {"target": r["target"],
                        "maks_tanya": int(r["maks_tanya"] or 0),
                        "maks_token": int(r["maks_token"] or 0),
                        "updated_at": r["updated_at"] or "",
                        "updated_by": r["updated_by"] or ""}
        finally:
            c.close()
    except Exception:
        pass
    return {"target": target, "maks_tanya": DEFAULT_MAKS_TANYA, "maks_token": 0,
            "updated_at": "", "updated_by": ""}


def list_quota():
    return [get_quota(t) for t in _TARGETS]


def set_quota(target, maks_tanya=None, maks_token=None, updated_by=""):
    target = (target or "").strip().lower()
    if target not in _TARGETS:
        return {"ok": False, "error": "Target kuota tidak valid."}
    cur = get_quota(target)
    try:
        mt = cur["maks_tanya"] if maks_tanya is None else max(0, int(maks_tanya))
        mtok = cur["maks_token"] if maks_token is None else max(0, int(maks_token))
    except Exception:
        return {"ok": False, "error": "Nilai kuota harus angka."}
    try:
        c = _c()
        try:
            res = c.execute(
                "UPDATE rag_quota SET maks_tanya=?, maks_token=?, updated_at=?, updated_by=? WHERE target=?",
                (mt, mtok, _now(), updated_by or "", target),
            )
            if res.rowcount == 0:
                c.execute(
                    "INSERT INTO rag_quota (target, maks_tanya, maks_token, updated_at, updated_by)"
                    " VALUES (?,?,?,?,?)",
                    (target, mt, mtok, _now(), updated_by or ""),
                )
            c.commit()
            return {"ok": True, "quota": get_quota(target)}
        finally:
            c.close()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Review feedback: daftar log chat RAG (filter nama / jempol / grounded / dst.)
# ---------------------------------------------------------------------------
def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _range_utc(preset, start="", end=""):
    """Rentang [lo, hi) UTC untuk preset hari kalender Asia/Jakarta."""
    preset = (preset or "all").strip().lower()
    now = datetime.now(timezone.utc)
    today = (now + timedelta(hours=7)).date()

    def day_start_utc(d):
        midnight_jkt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return midnight_jkt - timedelta(hours=7)

    if preset in ("", "all"):
        return None, None
    if preset == "custom":
        from datetime import date as _date

        def parse_d(s):
            try:
                y, m, d = [int(x) for x in str(s)[:10].split("-")]
                return _date(y, m, d)
            except Exception:
                return None
        ds = parse_d(start) or today
        de = parse_d(end) or ds
        return _fmt_dt(day_start_utc(ds)), _fmt_dt(day_start_utc(de) + timedelta(days=1))
    days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}.get(preset, 0)
    if days <= 0:
        return None, None
    lo = day_start_utc(today - timedelta(days=days - 1))
    hi = day_start_utc(today) + timedelta(days=1)
    return _fmt_dt(lo), _fmt_dt(hi)


def distinct_users(profil="agent"):
    try:
        c = _c()
        try:
            if profil:
                rows = c.execute(
                    "SELECT DISTINCT username FROM rag_chat_log "
                    "WHERE username!='' AND profil=? ORDER BY username",
                    (profil,)).fetchall()
            else:
                rows = c.execute(
                    "SELECT DISTINCT username FROM rag_chat_log "
                    "WHERE username!='' ORDER BY username").fetchall()
            return [r["username"] for r in rows]
        finally:
            c.close()
    except Exception:
        return []


def list_logs(username="", feedback="", grounded="", domain="", profil="agent",
              range_="all", start="", end="", q="", limit=300):
    """Daftar log chat RAG untuk review keandalan. Semua filter opsional.

    - username/q : cocokkan sebagian nama pengguna (agent)
    - feedback   : 'up' | 'down' | 'none' (belum dinilai) | '' (semua)
    - grounded   : '1'/'grounded' | '0'/'fallback' | '' (semua)
    - domain     : domain router (hukum/aplikasi/umum/...)
    - range_     : all | today | 7d | 30d | 90d | custom (+start/end YYYY-MM-DD)
    """
    where = ["1=1"]
    params = []
    if profil:
        where.append("profil=?"); params.append(profil)
    name = (username or q or "").strip()
    if name:
        where.append("lower(username) LIKE ?"); params.append("%" + name.lower() + "%")
    fb = (feedback or "").strip().lower()
    if fb in ("up", "down"):
        where.append("feedback=?"); params.append(fb)
    elif fb in ("none", "kosong", "belum"):
        where.append("(feedback IS NULL OR feedback='')")
    g = str(grounded or "").strip().lower()
    if g in ("1", "true", "grounded", "ya"):
        where.append("grounded=1")
    elif g in ("0", "false", "fallback", "tidak"):
        where.append("grounded=0")
    if domain:
        where.append("domain=?"); params.append(domain)
    lo, hi = _range_utc(range_, start, end)
    if lo:
        where.append("ts>=?"); params.append(lo)
    if hi:
        where.append("ts<?"); params.append(hi)
    w = " AND ".join(where)
    try:
        lim = max(1, min(int(limit or 300), 2000))
    except Exception:
        lim = 300
    try:
        c = _c()
        try:
            rows = c.execute(
                "SELECT id,ts,username,role,profil,question,answer,sources_json,"
                "grounded,domain,feedback,feedback_at FROM rag_chat_log WHERE "
                + w + " ORDER BY id DESC LIMIT ?", params + [lim]).fetchall()
            logs = []
            for r in rows:
                d = dict(r)
                try:
                    d["sources"] = json.loads(d.pop("sources_json") or "[]")
                except Exception:
                    d.pop("sources_json", None)
                    d["sources"] = []
                d["grounded"] = bool(d.get("grounded"))
                logs.append(d)
            stats = {"up": 0, "down": 0, "none": 0, "total": 0}
            for s in c.execute(
                    "SELECT COALESCE(NULLIF(feedback,''),'none') fb, COUNT(*) n "
                    "FROM rag_chat_log WHERE " + w + " GROUP BY fb", params).fetchall():
                key = s["fb"] if s["fb"] in ("up", "down") else "none"
                stats[key] = stats.get(key, 0) + int(s["n"])
                stats["total"] += int(s["n"])
            if profil:
                drows = c.execute(
                    "SELECT DISTINCT domain FROM rag_chat_log "
                    "WHERE domain!='' AND profil=? ORDER BY domain", (profil,)).fetchall()
            else:
                drows = c.execute(
                    "SELECT DISTINCT domain FROM rag_chat_log "
                    "WHERE domain!='' ORDER BY domain").fetchall()
            domains = [dr["domain"] for dr in drows]
        finally:
            c.close()
        return {"ok": True, "logs": logs, "total": len(logs), "stats": stats,
                "users": distinct_users(profil), "domains": domains}
    except Exception as e:
        return {"ok": False, "error": str(e), "logs": [],
                "stats": {"up": 0, "down": 0, "none": 0, "total": 0},
                "users": [], "domains": []}
