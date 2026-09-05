# -*- coding: utf-8 -*-
"""jobs.py — Fase 4: eksekusi ASINKRON + polling untuk jalur AGENTIC.

Masalah: loop agentic multi-langkah bisa lama -> rawan timeout gateway/proxy
(gejala: proxy membalas HTML 502/504 sehingga frontend gagal mem-parse JSON,
error "Unexpected token '<'"). Solusi: jalankan agentic.answer_agentic sebagai
BACKGROUND JOB (thread pool) dengan status tersimpan di SQLite, lalu frontend
polling status sampai selesai. Setiap request HTTP jadi cepat (tak blocking).

SIFAT: ADITIF & NON-BREAKING.
- Modul baru. Endpoint sinkron /api/ask-agentic lama TIDAK diubah.
- Memakai file DB yang sama dengan Laporan (reports.db) namun TABEL TERPISAH
  `agentic_jobs`; TIDAK menyentuh database sumber (tetap read-only via engine).
- Engine agentic (knowledge/agentic.py) TIDAK diubah.

Status job: queued -> running -> done | error | canceled.

Fase 5: saat job terminal, jejaknya dicatat ke audit persisten (PII di-mask)
lewat knowledge.guardrails.record — best-effort, tak pernah menggagalkan job.
"""
import os
import json
import uuid
import sqlite3
import threading
import datetime as _dt
from concurrent.futures import ThreadPoolExecutor

import db.reports_db as reports_db
from knowledge import agentic as agentic

# --- Batasan operasional -------------------------------------------------
MAX_WORKERS = int(os.environ.get("AGENTIC_JOB_WORKERS") or 3)   # job paralel
JOB_TTL_SECONDS = 24 * 3600     # job selesai dibersihkan setelah 24 jam

# Pakai file DB yang sama dengan Menu Laporan (reports.db) supaya tak menambah
# file baru; tabelnya terpisah (agentic_jobs).
DB_FILE = reports_db.DB_FILE

_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, MAX_WORKERS), thread_name_prefix="agentic-job")
_INIT_LOCK = threading.Lock()
_INITED = False


def _now():
    return _dt.datetime.now().isoformat(timespec="seconds")


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
        CREATE TABLE IF NOT EXISTS agentic_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            question TEXT,
            page TEXT,
            lang TEXT,
            max_iters INTEGER,
            result_json TEXT,
            error TEXT,
            steps_done INTEGER DEFAULT 0,
            databases TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agentic_jobs_status "
                 "ON agentic_jobs(status)")
    conn.commit()
    return conn


def _recover_orphans(conn):
    """Job non-terminal warisan proses sebelumnya (queued/running) tak punya
    worker lagi setelah restart -> tandai error supaya tidak menggantung."""
    try:
        now = _now()
        conn.execute(
            "UPDATE agentic_jobs SET status='error', "
            "error='terputus (proses aplikasi direstart)', "
            "updated_at=?, finished_at=? WHERE status IN ('queued','running')",
            (now, now),
        )
        conn.commit()
    except Exception:
        pass


def _cleanup(conn):
    """Hapus job selesai yang sudah kedaluwarsa (hemat ruang)."""
    try:
        cutoff = (_dt.datetime.now() - _dt.timedelta(
            seconds=JOB_TTL_SECONDS)).isoformat(timespec="seconds")
        conn.execute(
            "DELETE FROM agentic_jobs WHERE status IN "
            "('done','error','canceled') AND "
            "COALESCE(finished_at, updated_at) < ?", (cutoff,),
        )
        conn.commit()
    except Exception:
        pass


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
            _recover_orphans(conn)
            _cleanup(conn)
        finally:
            conn.close()
        _INITED = True


def _update(conn, job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(k + "=?" for k in fields)
    vals = list(fields.values()) + [job_id]
    conn.execute("UPDATE agentic_jobs SET " + cols + " WHERE id=?", vals)
    conn.commit()


def _status_of(conn, job_id):
    r = conn.execute("SELECT status FROM agentic_jobs WHERE id=?",
                     (job_id,)).fetchone()
    return r["status"] if r else None


def _audit_job(conn, job_id, status, result=None, error=None):
    """Fase 5: catat audit persisten (PII di-mask) saat job terminal.

    Best-effort total: guardrails di-import lazily & semua kegagalan ditelan
    agar TIDAK pernah mengganggu penyelesaian job.
    """
    try:
        from knowledge import guardrails as _guardrails
    except Exception:
        return
    try:
        r = conn.execute(
            "SELECT question, page, created_by, created_at, started_at, "
            "finished_at FROM agentic_jobs WHERE id=?", (job_id,)).fetchone()
        if not r:
            return
        dur = None
        try:
            t0 = r["started_at"] or r["created_at"]
            t1 = r["finished_at"] or _now()
            if t0 and t1:
                dur = int((_dt.datetime.fromisoformat(t1) -
                           _dt.datetime.fromisoformat(t0)).total_seconds() * 1000)
        except Exception:
            dur = None
        steps = (result or {}).get("steps") or []
        dbs = (result or {}).get("databases") or []
        _guardrails.record(
            job_id=job_id, created_by=r["created_by"], page=r["page"],
            status=status, question=r["question"], databases=dbs,
            steps=steps, error=error, duration_ms=dur)
    except Exception:
        pass


def _run(job_id, q_in, lang, max_iters):
    # Hormati pembatalan sebelum mulai.
    conn = connect()
    try:
        if _status_of(conn, job_id) == "canceled":
            return
        _update(conn, job_id, status="running", started_at=_now())
    finally:
        conn.close()

    result, err = None, None
    try:
        result = agentic.answer_agentic(q_in, lang, max_iters)
    except Exception as e:  # pragma: no cover - jaga-jaga
        err = str(e) or e.__class__.__name__

    conn = connect()
    try:
        if _status_of(conn, job_id) == "canceled":
            return  # buang hasil; pengguna sudah membatalkan
        if err is not None:
            _update(conn, job_id, status="error", error=err,
                    finished_at=_now())
        else:
            steps = (result or {}).get("steps") or []
            dbs = (result or {}).get("databases") or []
            _update(conn, job_id, status="done",
                    result_json=json.dumps(result, ensure_ascii=False),
                    steps_done=len(steps),
                    databases=json.dumps(dbs, ensure_ascii=False),
                    finished_at=_now())
        # Fase 5: audit persisten (best-effort, PII di-mask).
        _audit_job(conn, job_id,
                   status=("error" if err is not None else "done"),
                   result=result, error=err)
    finally:
        conn.close()


def start_job(q_in, lang=None, max_iters=None, question=None, page=None,
              created_by=""):
    """Buat job baru & jadwalkan eksekusi di thread pool. Return {job_id,status}."""
    _ensure()
    job_id = uuid.uuid4().hex
    now = _now()
    iters = int(max_iters or agentic.MAX_ITERS)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO agentic_jobs (id, status, question, page, lang, "
            "max_iters, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "queued", (question or q_in or "")[:2000], page or "",
             lang or "", iters, created_by or "", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        _EXECUTOR.submit(_run, job_id, q_in, lang, iters)
    except Exception as e:
        conn = connect()
        try:
            _update(conn, job_id, status="error",
                    error="gagal menjadwalkan job: " + str(e),
                    finished_at=_now())
        finally:
            conn.close()
    return {"job_id": job_id, "status": "queued"}


def get_job(job_id, with_result=True):
    _ensure()
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM agentic_jobs WHERE id=?",
                         (str(job_id),)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = {
        "id": r["id"],
        "status": r["status"],
        "question": r["question"],
        "page": r["page"],
        "steps_done": r["steps_done"] or 0,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
        "error": r["error"],
    }
    try:
        d["databases"] = json.loads(r["databases"] or "[]")
    except Exception:
        d["databases"] = []
    if with_result and r["status"] == "done":
        try:
            d["result"] = json.loads(r["result_json"] or "null")
        except Exception:
            d["result"] = None
    return d


def cancel_job(job_id):
    """Batalkan job. Efektif penuh untuk 'queued'; untuk 'running' hasilnya
    akan dibuang saat worker selesai (thread tak bisa dihentikan paksa)."""
    _ensure()
    conn = connect()
    try:
        st = _status_of(conn, str(job_id))
        if st is None:
            return {"ok": False, "error": "job tidak ditemukan"}
        if st in ("done", "error", "canceled"):
            return {"ok": True, "status": st, "note": "job sudah selesai"}
        _update(conn, str(job_id), status="canceled", finished_at=_now())
        return {"ok": True, "status": "canceled"}
    finally:
        conn.close()


if __name__ == "__main__":
    # Smoke test offline-safe: pakai file DB sementara + stub engine (tanpa LLM).
    import time as _time
    import tempfile

    DB_FILE = os.path.join(tempfile.gettempdir(), "agentic_jobs_smoke.db")
    try:
        os.remove(DB_FILE)
    except Exception:
        pass

    def _fake(q, lang=None, max_iters=6):
        _time.sleep(0.2)
        return {"ok": True, "mode": "agentic",
                "answer": "OK " + (q or "")[:10],
                "steps": [{"type": "query", "db": "analytics", "ok": True}],
                "databases": ["analytics"]}

    agentic.answer_agentic = _fake  # monkeypatch supaya tak perlu LLM/DB nyata

    j = start_job("Pertanyaan uji", question="Pertanyaan uji", page="dashboard")
    jid = j["job_id"]
    assert get_job(jid)["status"] in ("queued", "running"), "status awal salah"
    for _ in range(50):
        if get_job(jid)["status"] == "done":
            break
        _time.sleep(0.1)
    d = get_job(jid)
    assert d["status"] == "done", "job harus done, malah " + str(d["status"])
    assert d["result"]["answer"].startswith("OK"), "hasil tak sesuai"
    assert d["steps_done"] == 1, "steps_done salah"
    c = cancel_job(jid)
    assert c["ok"] and c["status"] == "done", "cancel job selesai salah"
    print("AGENTIC_JOBS_SMOKE_OK")
