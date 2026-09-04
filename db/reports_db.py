# -*- coding: utf-8 -*-
"""reports_db.py — Penyimpanan laporan (Menu Laporan, Opsi B / Fase 3).

DB internal TERPISAH (reports.db) untuk menyimpan laporan hasil AI agentic:
judul, permintaan, isi Markdown, daftar database sumber, dan jejak langkah.

SIFAT: ADITIF & NON-BREAKING — file & tabel baru; tidak menyentuh DB lain.
Ini SATU-SATUNYA tempat TULIS untuk fitur Laporan; database sumber tetap
read-only (dibaca lewat db.registry -> db.analytics_db.run_select).

Lokasi file: env PIPELINE_REPORTS_DB_FILE, default 'reports.db' (relatif CWD,
sejajar file .db lain di folder kerja aplikasi).
"""
import os
import json
import sqlite3
import datetime as _dt

DB_FILE = os.environ.get("PIPELINE_REPORTS_DB_FILE") or "reports.db"


def connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            question TEXT,
            content_md TEXT NOT NULL,
            databases TEXT,
            steps TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _now():
    return _dt.datetime.now().isoformat(timespec="seconds")


def _dumps(v):
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return "[]"


def _row_to_dict(r, with_content=True):
    d = {
        "id": r["id"],
        "title": r["title"],
        "question": r["question"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }
    try:
        d["databases"] = json.loads(r["databases"] or "[]")
    except Exception:
        d["databases"] = []
    if with_content:
        d["content_md"] = r["content_md"]
        try:
            d["steps"] = json.loads(r["steps"] or "[]")
        except Exception:
            d["steps"] = []
    return d


def create_report(conn, title, content_md, question="", databases=None,
                  steps=None, created_by=""):
    now = _now()
    cur = conn.execute(
        "INSERT INTO reports (title, question, content_md, databases, steps, "
        "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ((title or "Laporan").strip()[:300], question or "", content_md or "",
         _dumps(databases or []), _dumps(steps or []), created_by or "", now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_reports(conn, q=None, limit=200):
    limit = max(1, min(int(limit or 200), 1000))
    if q:
        like = "%" + q + "%"
        rows = conn.execute(
            "SELECT * FROM reports WHERE title LIKE ? OR question LIKE ? "
            "ORDER BY id DESC LIMIT ?", (like, like, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r, with_content=False) for r in rows]


def get_report(conn, rid):
    try:
        rid = int(rid)
    except Exception:
        return None
    r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return _row_to_dict(r, with_content=True) if r else None


def delete_report(conn, rid):
    try:
        rid = int(rid)
    except Exception:
        return False
    cur = conn.execute("DELETE FROM reports WHERE id=?", (rid,))
    conn.commit()
    return cur.rowcount > 0


if __name__ == "__main__":
    c = init_db(connect(":memory:"))
    i = create_report(c, "Uji", "# Uji\n\nIsi.", question="tes?",
                      databases=["analytics"],
                      steps=[{"type": "query", "db": "analytics", "ok": True}])
    assert get_report(c, i)["title"] == "Uji", "get_report gagal"
    assert list_reports(c)[0]["id"] == i, "list_reports gagal"
    assert delete_report(c, i) is True, "delete_report gagal"
    assert get_report(c, i) is None, "laporan seharusnya sudah terhapus"
    print("REPORTS_DB_SMOKE_OK")
