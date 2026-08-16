# -*- coding: utf-8 -*-
"""eval_recall_map.py — Peta Recall 'murah' per-intent, tersimpan PERMANEN.

Tujuan: memetakan intent mana yang SUDAH tertangani mesin RAG dan mana yang
BELUM, lalu menyimpannya permanen — tanpa harus menjalankan ulang seluruh
mesin->LLM->juri setiap kali. Berbeda dari Metode 1 (metrik agregat sekali
jalan), modul ini:
  * menyimpan status per-intent secara persisten (recall_intent/recall_phrase);
  * memanggil juri LLM HANYA saat pemetaan; intent yang sudah 'terjawab'
    TIDAK diuji & TIDAK dinilai ulang (hemat waktu + biaya);
  * bisa uji-ulang HANYA intent yang belum terjawab (only_unanswered);
  * status bisa dibatalkan/di-reset manual.

DEFINISI 'TERJAWAB' (ketat, sesuai kebutuhan): sebuah intent 'terjawab' bila
SEMUA variasi training phrase-nya dijawab mesin dan dinilai juri 'benar'. Jika
ada SATU saja phrase yang salah / halusinasi / abstain / tak-benar -> intent
BELUM terjawab.

Perbaikan gap dilakukan di luar modul ini (mis. menambah akronim/sinonim di
menu Kamus & Rewriting), lalu jalankan uji-ulang 'hanya yang belum'.
"""
import json
import uuid
import threading
import datetime as _dt

import eval_db
import eval_chatbot as ec

try:
    import eval_judge
except Exception:            # pragma: no cover
    eval_judge = None


_JOBS = {}
_LOCK = threading.Lock()


def _now():
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    return eval_db.connect()


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recall_run (
            id           TEXT PRIMARY KEY,
            profil       TEXT,
            params       TEXT,
            status       TEXT,
            n_total      INTEGER DEFAULT 0,
            n_done       INTEGER DEFAULT 0,
            started_at   TEXT,
            finished_at  TEXT,
            note         TEXT,
            metrik       TEXT
        );
        CREATE TABLE IF NOT EXISTS recall_intent (
            intent       TEXT PRIMARY KEY,
            status       TEXT DEFAULT 'belum',
            n_total      INTEGER DEFAULT 0,
            n_benar      INTEGER DEFAULT 0,
            n_salah      INTEGER DEFAULT 0,
            n_halusinasi INTEGER DEFAULT 0,
            n_abstain    INTEGER DEFAULT 0,
            gold         TEXT,
            last_run_id  TEXT,
            manual       INTEGER DEFAULT 0,
            updated_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS recall_phrase (
            intent       TEXT,
            phrase       TEXT,
            verdict      TEXT,
            skor         REAL,
            grounded     INTEGER DEFAULT 0,
            fallback_hit INTEGER DEFAULT 0,
            answer       TEXT,
            alasan       TEXT,
            latency_ms   REAL,
            run_id       TEXT,
            updated_at   TEXT,
            PRIMARY KEY (intent, phrase)
        );
        """
    )
    conn.commit()
    return conn


def _status_of(n_total, n_benar):
    """Terjawab HANYA bila ada frasa yang diuji dan SEMUANYA benar."""
    return "terjawab" if (n_total > 0 and n_benar == n_total) else "belum"


# ---------------------------------------------------------------- sampling
def _grouped_targets(top_n, window, lang, per_intent):
    """Kelompokkan sampel training phrase per intent memakai sampler Metode 1
    (sudah menyaring sampah + melewati intent testing)."""
    samples = ec.sample_intent_phrases(top_n, window, lang, per_intent)
    groups = {}
    for s in samples:
        it = (s.get("intent") or "").strip()
        if not it:
            continue
        g = groups.setdefault(it, {"gold": s.get("gold") or "", "phrases": []})
        ph = (s.get("pertanyaan") or "").strip()
        if ph and ph not in g["phrases"]:
            g["phrases"].append(ph)
    return groups


def _answered_set(conn):
    rows = conn.execute(
        "SELECT intent FROM recall_intent WHERE status='terjawab'").fetchall()
    return set((r[0] or "") for r in rows)


# ---------------------------------------------------------------- run mgmt
def _create_run(rid, profil, params, n_total):
    conn = init_db(connect())
    try:
        conn.execute(
            "INSERT INTO recall_run(id,profil,params,status,n_total,n_done,started_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (rid, profil, json.dumps(params or {}, ensure_ascii=False), "running",
             int(n_total), 0, _now()))
        conn.commit()
    finally:
        conn.close()


def _upsert_phrase(conn, intent, phrase, verdict, jr, r, run_id):
    conn.execute(
        "INSERT OR REPLACE INTO recall_phrase(intent,phrase,verdict,skor,grounded,"
        "fallback_hit,answer,alasan,latency_ms,run_id,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (intent, phrase, verdict, jr.get("skor"),
         int(bool(r.get("grounded"))), int(bool(r.get("fallback_hit"))),
         (r.get("answer") or "")[:4000], (jr.get("alasan") or "")[:500],
         r.get("latency_ms"), run_id, _now()))
    conn.commit()


def _upsert_intent(conn, intent, status, n_total, n_benar, n_salah, n_halu,
                   n_abst, gold, run_id, manual=0):
    conn.execute(
        "INSERT OR REPLACE INTO recall_intent(intent,status,n_total,n_benar,n_salah,"
        "n_halusinasi,n_abstain,gold,last_run_id,manual,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (intent, status, int(n_total), int(n_benar), int(n_salah), int(n_halu),
         int(n_abst), (gold or "")[:2000], run_id, int(manual), _now()))
    conn.commit()


def _worker(run_id, profil, groups, judge):
    conn = init_db(connect())
    profile = ec._profile(profil)
    done = 0
    try:
        for intent, g in groups.items():
            with _LOCK:
                job = _JOBS.get(run_id)
            if job and job.get("stop"):
                break
            gold = g.get("gold") or ""
            phrases = g.get("phrases") or []
            n_benar = n_salah = n_halu = n_abst = 0
            stopped = False
            for ph in phrases:
                with _LOCK:
                    job = _JOBS.get(run_id)
                if job and job.get("stop"):
                    stopped = True
                    break
                r = ec._run_one(profile, ph, history=None)
                jr = ec._judge_result(ph, gold, r) if (judge and r.get("ok")) else {}
                verdict = (jr.get("verdict") or "").strip()
                if verdict == "benar":
                    n_benar += 1
                elif verdict == "salah":
                    n_salah += 1
                elif verdict == "halusinasi":
                    n_halu += 1
                elif verdict.startswith("abstain"):
                    n_abst += 1
                else:
                    # tanpa juri / juri gagal: pakai fallback_hit sbg proksi 'abstain'.
                    if r.get("fallback_hit"):
                        n_abst += 1
                _upsert_phrase(conn, intent, ph, verdict, jr, r, run_id)
                done += 1
                conn.execute("UPDATE recall_run SET n_done=? WHERE id=?", (done, run_id))
                conn.commit()
            if stopped:
                break
            # Finalisasi status intent HANYA bila seluruh frasanya sempat diuji.
            status = _status_of(len(phrases), n_benar)
            _upsert_intent(conn, intent, status, len(phrases), n_benar, n_salah,
                           n_halu, n_abst, gold, run_id, manual=0)
        metr = _summary(conn)
        conn.execute("UPDATE recall_run SET status=?, finished_at=?, metrik=? WHERE id=?",
                     ("done", _now(), json.dumps(metr, ensure_ascii=False), run_id))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE recall_run SET status=?, finished_at=?, note=? WHERE id=?",
                     ("error", _now(), str(e)[:300], run_id))
        conn.commit()
    finally:
        conn.close()
        with _LOCK:
            _JOBS.pop(run_id, None)


def start_map(profil="chatbot", top_n=100, window="90d", lang=None,
              per_intent=12, only_unanswered=True, judge=True, limit=None):
    conn = init_db(connect())
    try:
        groups = _grouped_targets(top_n, window, lang, per_intent)
        if only_unanswered:
            done = _answered_set(conn)
            groups = {k: v for k, v in groups.items() if k not in done}
        if limit:
            try:
                groups = dict(list(groups.items())[: int(limit)])
            except Exception:
                pass
        n_total = sum(len(v["phrases"]) for v in groups.values())
    finally:
        conn.close()
    if not groups or n_total == 0:
        return {"ok": True, "run_id": None, "n_total": 0, "n_intent": 0,
                "note": "Tidak ada intent untuk dipetakan (semua sudah 'terjawab' "
                        "atau katalog kosong — sinkronkan Peta Intent lebih dulu)."}
    rid = "recall_" + uuid.uuid4().hex[:10]
    _create_run(rid, profil, {"top_n": top_n, "window": window,
                              "per_intent": per_intent,
                              "only_unanswered": bool(only_unanswered),
                              "judge": bool(judge)}, n_total)
    with _LOCK:
        _JOBS[rid] = {"stop": False}
    threading.Thread(target=_worker, args=(rid, profil, groups, bool(judge)),
                     daemon=True).start()
    return {"ok": True, "run_id": rid, "n_total": n_total, "n_intent": len(groups)}


def stop(run_id):
    with _LOCK:
        j = _JOBS.get(run_id)
        if j:
            j["stop"] = True
    return {"ok": True, "run_id": run_id}


def _decode(d):
    for k in ("params", "metrik"):
        try:
            d[k] = json.loads(d.get(k) or "null")
        except Exception:
            d[k] = None
    return d


def status(run_id):
    conn = init_db(connect())
    try:
        r = conn.execute("SELECT * FROM recall_run WHERE id=?", (run_id,)).fetchone()
        if not r:
            return {"ok": False, "error": "run tidak ditemukan"}
        return {"ok": True, "run": _decode(dict(r))}
    finally:
        conn.close()


def _summary(conn):
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM recall_intent GROUP BY status").fetchall()
    by = {}
    for r in rows:
        by[(r[0] or "belum")] = int(r[1])
    total = sum(by.values())
    terjawab = by.get("terjawab", 0)
    return {"total_intent": total, "terjawab": terjawab,
            "belum": total - terjawab, "by_status": by,
            "pct_terjawab": round(terjawab / total * 100.0, 2) if total else 0.0}


def summary():
    conn = init_db(connect())
    try:
        return {"ok": True, "metrik": _summary(conn)}
    finally:
        conn.close()


def get_map(status=None, q=None, limit=1000):
    conn = init_db(connect())
    try:
        sql = ("SELECT intent,status,n_total,n_benar,n_salah,n_halusinasi,n_abstain,"
               "last_run_id,manual,updated_at FROM recall_intent")
        where, params = [], []
        if status:
            where.append("status=?"); params.append(status)
        if q:
            where.append("intent LIKE ?"); params.append("%" + q + "%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY (status='terjawab') ASC, intent ASC LIMIT ?"
        params.append(int(limit))
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"ok": True, "results": rows, "metrik": _summary(conn)}
    finally:
        conn.close()


def get_intent(intent):
    conn = init_db(connect())
    try:
        it = conn.execute("SELECT * FROM recall_intent WHERE intent=?",
                          (intent,)).fetchone()
        rows = [dict(r) for r in conn.execute(
            "SELECT intent,phrase,verdict,skor,grounded,fallback_hit,answer,alasan,"
            "latency_ms,updated_at FROM recall_phrase WHERE intent=? "
            "ORDER BY (verdict='benar') ASC, phrase ASC", (intent,)).fetchall()]
        return {"ok": True, "intent": (dict(it) if it else None), "phrases": rows}
    finally:
        conn.close()


def reset_status(intent=None, all_intents=False):
    """Batalkan status 'terjawab' (di-set kembali 'belum', ditandai manual=1)
    agar ikut diuji ulang pada mode 'hanya yang belum'."""
    conn = init_db(connect())
    try:
        if all_intents:
            conn.execute("UPDATE recall_intent SET status='belum', manual=1, "
                         "updated_at=? WHERE status='terjawab'", (_now(),))
            conn.commit()
            return {"ok": True, "reset": "all"}
        if not intent:
            return {"ok": False, "error": "intent kosong"}
        cur = conn.execute("UPDATE recall_intent SET status='belum', manual=1, "
                           "updated_at=? WHERE intent=?", (_now(), intent))
        conn.commit()
        return {"ok": True, "reset": intent, "changed": cur.rowcount}
    finally:
        conn.close()
