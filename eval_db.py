# -*- coding: utf-8 -*-
"""eval_db.py — Penyimpanan (SQLite) untuk evaluasi keandalan mesin RAG.

Menyimpan:
  - eval_sample : sampel pertanyaan uji (livechat + chatbot) + gold (livechat)
  - eval_run    : satu sesi evaluasi (profil + parameter + progres)
  - eval_result : hasil per sampel (jawaban RAG, verdict penilai, validasi manusia)

Metrik utama (sesuai kesepakatan peluncuran):
  keandalan  = %(benar & grounded) + %(abstain yang benar)   -> target >= 90%
  halusinasi = %(jawaban mengarang / tak terdukung)           -> gerbang < 3%

Hanya stdlib (sqlite3). DB default: eval.db (env PIPELINE_EVAL_DB_FILE).
"""
import os
import json
import hashlib
import sqlite3
import datetime as _dt

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Kosakata verdict dipakai di seluruh harness/dashboard.
VERDICTS = ("benar", "salah", "halusinasi", "abstain_benar", "abstain_salah")
# Verdict yang dihitung sebagai "andal".
GOOD_VERDICTS = ("benar", "abstain_benar")


def default_db_path():
    return os.environ.get("PIPELINE_EVAL_DB_FILE") or os.path.join(_BASE_DIR, "eval.db")


def _now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=8000;")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS eval_sample (
            id          TEXT PRIMARY KEY,
            jenis       TEXT,
            sumber_ref  TEXT,
            pertanyaan  TEXT,
            gold        TEXT,
            label       TEXT,
            meta        TEXT,
            holdout     INTEGER DEFAULT 1,
            created_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_es_jenis ON eval_sample(jenis);
        CREATE INDEX IF NOT EXISTS idx_es_label ON eval_sample(label);

        CREATE TABLE IF NOT EXISTS eval_run (
            id          TEXT PRIMARY KEY,
            profil      TEXT,
            jenis       TEXT,
            params      TEXT,
            status      TEXT,
            n_total     INTEGER DEFAULT 0,
            n_done      INTEGER DEFAULT 0,
            started_at  TEXT,
            finished_at TEXT,
            note        TEXT,
            sweep_id    TEXT,
            min_cos     REAL
        );

        CREATE TABLE IF NOT EXISTS eval_result (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id         TEXT,
            sample_id      TEXT,
            jenis          TEXT,
            profil         TEXT,
            pertanyaan     TEXT,
            gold           TEXT,
            answer         TEXT,
            grounded       INTEGER DEFAULT 0,
            abstain        INTEGER DEFAULT 0,
            sources        TEXT,
            domain         TEXT,
            judge_verdict  TEXT,
            judge_grounded INTEGER DEFAULT 0,
            judge_skor     REAL,
            judge_alasan   TEXT,
            human_verdict  TEXT,
            human_note     TEXT,
            created_at     TEXT,
            UNIQUE(run_id, sample_id)
        );
        CREATE INDEX IF NOT EXISTS idx_er_run ON eval_result(run_id);
        CREATE INDEX IF NOT EXISTS idx_er_verdict ON eval_result(judge_verdict);
        """
    )
    # Migrasi non-destruktif: tambah kolom sweep (kalibrasi Point 3) bila DB lama.
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(eval_run)").fetchall()]
    if "sweep_id" not in _cols:
        conn.execute("ALTER TABLE eval_run ADD COLUMN sweep_id TEXT")
    if "min_cos" not in _cols:
        conn.execute("ALTER TABLE eval_run ADD COLUMN min_cos REAL")
    conn.commit()
    return conn


def sample_id(jenis, pertanyaan):
    basis = (str(jenis) + "|" + (pertanyaan or "").strip().lower())
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def upsert_sample(conn, jenis, pertanyaan, gold=None, label="", sumber_ref="", meta=None, holdout=1):
    sid = sample_id(jenis, pertanyaan)
    conn.execute(
        "INSERT INTO eval_sample(id,jenis,sumber_ref,pertanyaan,gold,label,meta,holdout,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "gold=excluded.gold, label=excluded.label, sumber_ref=excluded.sumber_ref, meta=excluded.meta",
        (sid, jenis, sumber_ref or "", (pertanyaan or "").strip(), gold, label or "",
         json.dumps(meta or {}, ensure_ascii=False), int(holdout), _now()),
    )
    return sid


def list_samples(conn, jenis=None, limit=None):
    q = "SELECT * FROM eval_sample"
    params = []
    if jenis and jenis != "all":
        q += " WHERE jenis=?"
        params.append(jenis)
    q += " ORDER BY label, id"
    if limit:
        q += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def sample_counts(conn):
    rows = conn.execute("SELECT jenis, COUNT(*) AS n FROM eval_sample GROUP BY jenis").fetchall()
    out = {"livechat": 0, "chatbot": 0, "total": 0}
    for r in rows:
        out[r["jenis"]] = r["n"]
        out["total"] += r["n"]
    return out


def clear_samples(conn, jenis=None):
    if jenis and jenis != "all":
        conn.execute("DELETE FROM eval_sample WHERE jenis=?", (jenis,))
    else:
        conn.execute("DELETE FROM eval_sample")
    conn.commit()


# ---- runs ----
def create_run(conn, run_id, profil, jenis, params, n_total, sweep_id=None, min_cos=None):
    conn.execute(
        "INSERT INTO eval_run(id,profil,jenis,params,status,n_total,n_done,started_at,sweep_id,min_cos) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (run_id, profil, jenis, json.dumps(params or {}, ensure_ascii=False),
         "running", int(n_total), 0, _now(), sweep_id,
         (float(min_cos) if min_cos is not None else None)),
    )
    conn.commit()


def bump_run(conn, run_id, n_done):
    conn.execute("UPDATE eval_run SET n_done=? WHERE id=?", (int(n_done), run_id))
    conn.commit()


def finish_run(conn, run_id, status="done", note=""):
    conn.execute("UPDATE eval_run SET status=?, finished_at=?, note=? WHERE id=?",
                 (status, _now(), note or "", run_id))
    conn.commit()


def get_run(conn, run_id):
    r = conn.execute("SELECT * FROM eval_run WHERE id=?", (run_id,)).fetchone()
    return dict(r) if r else None


def list_runs(conn, limit=25):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM eval_run ORDER BY started_at DESC LIMIT ?", (int(limit),)).fetchall()]


def latest_run(conn, status="done"):
    r = conn.execute("SELECT * FROM eval_run WHERE status=? ORDER BY started_at DESC LIMIT 1",
                     (status,)).fetchone()
    return dict(r) if r else None


def list_sweep(conn, sweep_id):
    """Semua run milik satu sweep kalibrasi, urut ambang cosine menaik."""
    rows = conn.execute(
        "SELECT * FROM eval_run WHERE sweep_id=? ORDER BY min_cos", (sweep_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---- results ----
def save_result(conn, run_id, rec):
    conn.execute(
        "INSERT INTO eval_result(run_id,sample_id,jenis,profil,pertanyaan,gold,answer,"
        "grounded,abstain,sources,domain,judge_verdict,judge_grounded,judge_skor,judge_alasan,"
        "human_verdict,human_note,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,sample_id) DO UPDATE SET "
        "answer=excluded.answer, grounded=excluded.grounded, abstain=excluded.abstain, "
        "sources=excluded.sources, domain=excluded.domain, judge_verdict=excluded.judge_verdict, "
        "judge_grounded=excluded.judge_grounded, judge_skor=excluded.judge_skor, "
        "judge_alasan=excluded.judge_alasan",
        (run_id, rec.get("sample_id"), rec.get("jenis"), rec.get("profil"),
         rec.get("pertanyaan"), rec.get("gold"), rec.get("answer"),
         int(bool(rec.get("grounded"))), int(bool(rec.get("abstain"))),
         json.dumps(rec.get("sources") or [], ensure_ascii=False), rec.get("domain") or "",
         rec.get("judge_verdict") or "", int(bool(rec.get("judge_grounded"))),
         rec.get("judge_skor"), rec.get("judge_alasan") or "",
         rec.get("human_verdict"), rec.get("human_note"), _now()),
    )
    conn.commit()


def list_results(conn, run_id, only=None):
    rows = conn.execute("SELECT * FROM eval_result WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.get("sources") or "[]")
        except Exception:
            d["sources"] = []
        d["verdict_efektif"] = d.get("human_verdict") or d.get("judge_verdict") or ""
        out.append(d)
    if only == "fail":
        out = [d for d in out if d["verdict_efektif"] not in GOOD_VERDICTS]
    return out


def set_human_verdict(conn, result_id, verdict, note=""):
    v = (verdict or "").strip()
    if v and v not in VERDICTS:
        return {"ok": False, "error": "verdict tak dikenal."}
    conn.execute("UPDATE eval_result SET human_verdict=?, human_note=? WHERE id=?",
                 (v or None, note or "", int(result_id)))
    conn.commit()
    return {"ok": True}


def metrics(conn, run_id):
    rows = list_results(conn, run_id)
    total = len(rows)
    agg = {"total": total, "counts": {v: 0 for v in VERDICTS},
           "by_jenis": {}, "by_domain": {}, "human_validated": 0}
    good = hall = 0
    skor_sum = 0.0
    skor_n = 0
    for d in rows:
        v = d["verdict_efektif"]
        if v in agg["counts"]:
            agg["counts"][v] += 1
        if v in GOOD_VERDICTS:
            good += 1
        if v == "halusinasi":
            hall += 1
        if d.get("human_verdict"):
            agg["human_validated"] += 1
        if d.get("judge_skor") is not None:
            skor_sum += float(d["judge_skor"]); skor_n += 1
        j = d.get("jenis") or "?"
        bj = agg["by_jenis"].setdefault(j, {"total": 0, "good": 0, "hall": 0})
        bj["total"] += 1
        if v in GOOD_VERDICTS: bj["good"] += 1
        if v == "halusinasi": bj["hall"] += 1
        dm = d.get("domain") or "?"
        bd = agg["by_domain"].setdefault(dm, {"total": 0, "good": 0, "hall": 0})
        bd["total"] += 1
        if v in GOOD_VERDICTS: bd["good"] += 1
        if v == "halusinasi": bd["hall"] += 1
    agg["keandalan"] = round(good / total * 100.0, 2) if total else 0.0
    agg["halusinasi_rate"] = round(hall / total * 100.0, 2) if total else 0.0
    agg["skor_rata"] = round(skor_sum / skor_n, 3) if skor_n else None

    def _rate(b):
        b["keandalan"] = round(b["good"] / b["total"] * 100.0, 2) if b["total"] else 0.0
        b["halusinasi_rate"] = round(b["hall"] / b["total"] * 100.0, 2) if b["total"] else 0.0
    for b in agg["by_jenis"].values():
        _rate(b)
    for b in agg["by_domain"].values():
        _rate(b)
    return agg
