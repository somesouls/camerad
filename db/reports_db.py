# -*- coding: utf-8 -*-
"""reports_db.py — Penyimpanan laporan (Menu Laporan, Opsi B / Fase 3+7).

DB internal TERPISAH (reports.db) untuk menyimpan laporan hasil AI agentic:
judul, permintaan, isi Markdown, daftar database sumber, dan jejak langkah.

Fase 7 (aditif): laporan bisa DIEDIT manual & DIPERBARUI oleh AI; setiap
perubahan menyimpan snapshot isi lama ke tabel `report_versions` sehingga
riwayat versi bisa dilihat & dipulihkan.

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
    # Fase 7: riwayat versi — snapshot isi laporan SEBELUM tiap perubahan.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            title TEXT,
            question TEXT,
            content_md TEXT,
            source TEXT,
            note TEXT,
            editor TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_versions_report "
        "ON report_versions(report_id)"
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


def snapshot_version(conn, rid, source="edit", note="", editor=""):
    """Simpan snapshot isi laporan SAAT INI ke report_versions (Fase 7).

    Dipanggil sebelum update_report menimpa isi, agar versi lama tersimpan.
    Return id versi baru, atau None bila laporan tak ditemukan.
    """
    try:
        rid = int(rid)
    except Exception:
        return None
    r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    cur = conn.execute(
        "INSERT INTO report_versions (report_id, title, question, content_md, "
        "source, note, editor, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (rid, r["title"], r["question"], r["content_md"], source or "edit",
         (note or "")[:300], editor or "", _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_report(conn, rid, title=None, content_md=None, question=None,
                  databases=None, editor="", note="", source="edit",
                  snapshot=True):
    """Perbarui laporan (Fase 7). Field bernilai None dibiarkan apa adanya.

    Bila snapshot=True, isi lama disimpan dulu ke report_versions.
    Return True bila laporan ditemukan & diperbarui.
    """
    try:
        rid = int(rid)
    except Exception:
        return False
    r = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if not r:
        return False
    if snapshot:
        snapshot_version(conn, rid, source=source, note=note, editor=editor)
    sets = []
    vals = []
    if title is not None:
        sets.append("title=?")
        vals.append((title or "Laporan").strip()[:300])
    if content_md is not None:
        sets.append("content_md=?")
        vals.append(content_md or "")
    if question is not None:
        sets.append("question=?")
        vals.append(question or "")
    if databases is not None:
        sets.append("databases=?")
        vals.append(_dumps(databases or []))
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(rid)
    conn.execute("UPDATE reports SET " + ", ".join(sets) + " WHERE id=?", vals)
    conn.commit()
    return True


def list_versions(conn, rid, limit=100):
    try:
        rid = int(rid)
    except Exception:
        return []
    limit = max(1, min(int(limit or 100), 500))
    rows = conn.execute(
        "SELECT id, report_id, title, source, note, editor, created_at, "
        "length(content_md) AS size FROM report_versions WHERE report_id=? "
        "ORDER BY id DESC LIMIT ?", (rid, limit),
    ).fetchall()
    return [{
        "id": r["id"], "report_id": r["report_id"], "title": r["title"],
        "source": r["source"], "note": r["note"], "editor": r["editor"],
        "created_at": r["created_at"], "size": r["size"] or 0,
    } for r in rows]


def get_version(conn, vid):
    try:
        vid = int(vid)
    except Exception:
        return None
    r = conn.execute("SELECT * FROM report_versions WHERE id=?", (vid,)).fetchone()
    if not r:
        return None
    return {
        "id": r["id"], "report_id": r["report_id"], "title": r["title"],
        "question": r["question"], "content_md": r["content_md"],
        "source": r["source"], "note": r["note"], "editor": r["editor"],
        "created_at": r["created_at"],
    }


def delete_report(conn, rid):
    try:
        rid = int(rid)
    except Exception:
        return False
    conn.execute("DELETE FROM report_versions WHERE report_id=?", (rid,))
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
    # Fase 7: update manual + snapshot versi.
    assert update_report(c, i, content_md="# Uji\n\nIsi baru.",
                         note="edit manual", editor="tester") is True, "update gagal"
    assert get_report(c, i)["content_md"].endswith("Isi baru."), "isi tak terupdate"
    vs = list_versions(c, i)
    assert len(vs) == 1, "harus ada 1 versi snapshot"
    v = get_version(c, vs[0]["id"])
    assert v and v["content_md"].endswith("Isi."), "isi versi lama salah"
    # Pulihkan versi lama.
    assert update_report(c, i, content_md=v["content_md"], source="restore",
                         note="pulihkan") is True, "restore gagal"
    assert get_report(c, i)["content_md"].endswith("Isi."), "restore tak berlaku"
    assert len(list_versions(c, i)) == 2, "restore juga harus snapshot"
    assert delete_report(c, i) is True, "delete_report gagal"
    assert get_report(c, i) is None, "laporan seharusnya sudah terhapus"
    assert list_versions(c, i) == [], "versi harus ikut terhapus"
    print("REPORTS_DB_SMOKE_OK")
