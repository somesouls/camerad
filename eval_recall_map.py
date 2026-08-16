# -*- coding: utf-8 -*-
"""eval_recall_map.py — Metode 1: Peta Recall per-intent (tersimpan PERMANEN).

Tujuan Metode 1 (sekaligus SATU-SATUNYA isi menu ini): memetakan TOP INTENT
Dialogflow yang sering terpanggil, lalu menguji apakah mesin RAG->LLM sudah
mampu menjawab training phrase yang selama ini sudah dikenali bot lama. Ini
seperti "lab uji" otomatis: tiap training phrase diproses mesin RAG -> dirakit
prompt -> dilempar ke LLM -> menghasilkan jawaban -> dinilai juri LLM
(benar/salah/halusinasi/abstain). Tanpa input manual satu per satu.

Yang disimpan permanen (eval.db):
  * recall_intent  : status + rekap per intent (Benar/Total, masalah, cukup).
  * recall_phrase  : per training phrase -> prompt lengkap yang dirakit mesin
                     RAG | jawaban LLM | verdict + alasan juri LLM.

DEFINISI 'TERJAWAB' (ketat): sebuah intent 'terjawab' bila SEMUA training
phrase-nya dijawab & dinilai juri 'benar'. Bila ada SATU saja yang salah /
halusinasi / abstain / tak-benar -> intent BELUM terjawab.

DEFINISI 'CUKUP' (keputusan admin apakah perlu diuji lagi):
  * 'terjawab' (semua benar) OTOMATIS dianggap cukup; ATAU
  * admin menandai manual 'cukup' (kolom cukup=1) walau belum 100% benar
    (mis. sudah benar 4/5 dan admin menilai itu memadai).
Uji-ulang 'hanya yang belum' MELEWATI intent ber-status 'terjawab' saja. Flag
manual 'cukup' TIDAK menghentikan uji-ulang: intent yang ditandai cukup tapi
belum 'terjawab' tetap ikut diuji ulang. Tersedia juga opsi menguji ulang
intent yang sudah 'terjawab' (only_unanswered=False).

Perbaikan gap dilakukan di luar modul ini (mis. menambah akronim/sinonim di
menu Kamus & Rewriting), lalu jalankan uji-ulang 'hanya yang belum'.

Mode uji MENGIKUTI pengaturan profil pada halaman konfigurasinya
(honor_mode=True) — bukan memaksa pipeline penuh — supaya hasilnya mencerminkan
perilaku produksi. Prompt lengkap yang dirakit mesin ditangkap lewat
diagnostics.
"""
import json
import time
import uuid
import threading
import datetime as _dt

import eval_db
import eval_chatbot as ec
import rag_engine
import rag_config_db as rcfg

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


def _ensure_columns(conn):
    """Migrasi ringan: tambah kolom baru pada instalasi lama (idempoten)."""
    try:
        pc = {r[1] for r in conn.execute("PRAGMA table_info(recall_phrase)").fetchall()}
        if "prompt" not in pc:
            conn.execute("ALTER TABLE recall_phrase ADD COLUMN prompt TEXT")
    except Exception:
        pass
    try:
        ic = {r[1] for r in conn.execute("PRAGMA table_info(recall_intent)").fetchall()}
        if "cukup" not in ic:
            conn.execute("ALTER TABLE recall_intent ADD COLUMN cukup INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.commit()
    except Exception:
        pass


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
            cukup        INTEGER DEFAULT 0,
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
            prompt       TEXT,
            run_id       TEXT,
            updated_at   TEXT,
            PRIMARY KEY (intent, phrase)
        );
        """
    )
    conn.commit()
    _ensure_columns(conn)
    return conn


def _status_of(n_total, n_benar):
    """Terjawab HANYA bila ada frasa yang diuji dan SEMUANYA benar."""
    return "terjawab" if (n_total > 0 and n_benar == n_total) else "belum"


# ---------------------------------------------------------------- uji satu frasa
def _run_one_diag(profile, question, history=None):
    """Jalankan satu training phrase lewat mesin RAG dengan diagnostics AKTIF
    agar 'prompt lengkap yang dirakit mesin' (prompt_final) ikut tertangkap.
    honor_mode=True -> mengikuti mode NYATA profil (pengaturan halaman
    konfigurasinya), bukan memaksa pipeline penuh."""
    t0 = time.time()
    try:
        res = rag_engine.answer(question, profile, override=None,
                                history=history, diagnostics=True, honor_mode=True)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "answer": "",
                "grounded": False, "abstain": True, "fallback_hit": True,
                "domain": "", "sources": [], "prompt": "",
                "latency_ms": (time.time() - t0) * 1000.0}
    dt = (time.time() - t0) * 1000.0
    res = res or {}
    grounded = bool(res.get("grounded"))
    ans = res.get("answer") or ""
    fb = (profile.get("fallback") or rcfg.FALLBACK_DEFAULT)
    fallback_hit = (not grounded) or (bool(ans.strip()) and ans.strip() == (fb or "").strip())
    return {"ok": bool(res.get("ok", True)), "answer": ans, "grounded": grounded,
            "abstain": (not grounded), "fallback_hit": bool(fallback_hit),
            "domain": res.get("domain") or "", "sources": res.get("sources") or [],
            "prompt": res.get("prompt_final") or "", "latency_ms": dt}


# ---------------------------------------------------------------- sampling
def _grouped_targets(top_n, window, lang, per_intent):
    """Kelompokkan sampel training phrase per intent memakai sampler bersama
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
        "fallback_hit,answer,alasan,latency_ms,prompt,run_id,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (intent, phrase, verdict, jr.get("skor"),
         int(bool(r.get("grounded"))), int(bool(r.get("fallback_hit"))),
         (r.get("answer") or "")[:4000], (jr.get("alasan") or "")[:500],
         r.get("latency_ms"), (r.get("prompt") or "")[:8000], run_id, _now()))
    conn.commit()


def _upsert_intent(conn, intent, status, n_total, n_benar, n_salah, n_halu,
                   n_abst, gold, run_id, manual=0, cukup=None):
    """Simpan rekap per intent. cukup=None -> pertahankan flag manual 'cukup'
    yang sudah ada agar tidak tertimpa saat uji-ulang."""
    if cukup is None:
        row = conn.execute("SELECT cukup FROM recall_intent WHERE intent=?",
                           (intent,)).fetchone()
        cukup = int(row[0]) if (row and row[0] is not None) else 0
    conn.execute(
        "INSERT OR REPLACE INTO recall_intent(intent,status,n_total,n_benar,n_salah,"
        "n_halusinasi,n_abstain,gold,last_run_id,manual,cukup,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (intent, status, int(n_total), int(n_benar), int(n_salah), int(n_halu),
         int(n_abst), (gold or "")[:2000], run_id, int(manual), int(cukup), _now()))
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
                r = _run_one_diag(profile, ph, history=None)
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
                "note": "Tidak ada intent untuk diuji (semua sudah 'terjawab' "
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
    try:
        cukup = conn.execute(
            "SELECT COUNT(*) FROM recall_intent "
            "WHERE status='terjawab' OR COALESCE(cukup,0)=1").fetchone()[0]
    except Exception:
        cukup = terjawab
    return {"total_intent": total, "terjawab": terjawab,
            "belum": total - terjawab, "cukup": int(cukup), "by_status": by,
            "pct_terjawab": round(terjawab / total * 100.0, 2) if total else 0.0,
            "pct_cukup": round(cukup / total * 100.0, 2) if total else 0.0}


def summary():
    conn = init_db(connect())
    try:
        return {"ok": True, "metrik": _summary(conn)}
    finally:
        conn.close()


def get_map(status=None, q=None, limit=50, offset=0, cukup=None):
    """Daftar intent (pagination). cukup=True -> hanya yang cukup (terjawab ATAU
    ditandai manual); cukup=False -> hanya yang BELUM cukup."""
    conn = init_db(connect())
    try:
        base = " FROM recall_intent"
        where, params = [], []
        if status:
            where.append("status=?"); params.append(status)
        if q:
            where.append("intent LIKE ?"); params.append("%" + q + "%")
        if cukup is True:
            where.append("(status='terjawab' OR COALESCE(cukup,0)=1)")
        elif cukup is False:
            where.append("(status!='terjawab' AND COALESCE(cukup,0)=0)")
        wsql = (" WHERE " + " AND ".join(where)) if where else ""
        try:
            total = conn.execute("SELECT COUNT(*)" + base + wsql, params).fetchone()[0]
        except Exception:
            total = 0
        sql = ("SELECT intent,status,n_total,n_benar,n_salah,n_halusinasi,n_abstain,"
               "last_run_id,manual,cukup,updated_at" + base + wsql +
               " ORDER BY (status='terjawab') ASC, (COALESCE(cukup,0)=1) ASC, intent ASC"
               " LIMIT ? OFFSET ?")
        rows = [dict(r) for r in conn.execute(
            sql, params + [int(limit), int(offset)]).fetchall()]
        return {"ok": True, "results": rows, "total": int(total),
                "limit": int(limit), "offset": int(offset), "metrik": _summary(conn)}
    finally:
        conn.close()


def get_intent(intent):
    conn = init_db(connect())
    try:
        it = conn.execute("SELECT * FROM recall_intent WHERE intent=?",
                          (intent,)).fetchone()
        rows = [dict(r) for r in conn.execute(
            "SELECT intent,phrase,verdict,skor,grounded,fallback_hit,answer,alasan,"
            "latency_ms,prompt,updated_at FROM recall_phrase WHERE intent=? "
            "ORDER BY (verdict='benar') ASC, phrase ASC", (intent,)).fetchall()]
        return {"ok": True, "intent": (dict(it) if it else None), "phrases": rows}
    finally:
        conn.close()


def set_cukup(intent=None, cukup=True, all_intents=False):
    """Tandai/lepas flag manual 'cukup' (keputusan admin). Flag ini TIDAK
    mengubah status 'terjawab' dan TIDAK menghentikan uji-ulang bila intent
    belum 'terjawab' — hanya penanda keputusan admin untuk tampilan/keputusan."""
    conn = init_db(connect())
    try:
        val = 1 if cukup else 0
        if all_intents:
            conn.execute("UPDATE recall_intent SET cukup=?, updated_at=?",
                         (val, _now()))
            conn.commit()
            return {"ok": True, "cukup": val, "scope": "all"}
        if not intent:
            return {"ok": False, "error": "intent kosong"}
        cur = conn.execute("UPDATE recall_intent SET cukup=?, updated_at=? WHERE intent=?",
                           (val, _now(), intent))
        conn.commit()
        return {"ok": True, "cukup": val, "intent": intent, "changed": cur.rowcount}
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
