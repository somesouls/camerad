# -*- coding: utf-8 -*-
"""eval_chatbot.py — Evaluasi KHUSUS profil chatbot (terpisah dari profil agent).

Tiga metode pengujian:
  1) chatbot_intent   : coverage training-phrase dari TOP INTENT (analytics_db.top_intents)
                        + soft-gold jawaban resmi intent (intentmap_catalog.jawaban_cuplikan).
  2) chatbot_fallback : deflection pertanyaan fallback (>N kali) — giliran pertama diuji
                        tanpa riwayat; giliran lanjutan diuji DENGAN riwayat percakapan.
  3) chatbot_load     : uji beban/concurrency mesin RAG (latensi p50/p95, throughput, error).

Menyimpan di DB evaluasi yang sama (PIPELINE_EVAL_DB_FILE) pada tabel evalc_run/evalc_result,
TERPISAH dari tabel agent (eval_run/eval_result) agar tidak mengganggu menu Evaluasi Agent.
"""
import json
import math
import re
import time
import uuid
import threading
import datetime as _dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import eval_db
import analytics_db as adb
import rag_config_db as rcfg
import rag_engine

try:
    import intentmap_db as imdb
except Exception:            # pragma: no cover
    imdb = None
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
        CREATE TABLE IF NOT EXISTS evalc_run (
            id          TEXT PRIMARY KEY,
            metode      TEXT,
            profil      TEXT,
            params      TEXT,
            status      TEXT,
            n_total     INTEGER DEFAULT 0,
            n_done      INTEGER DEFAULT 0,
            started_at  TEXT,
            finished_at TEXT,
            note        TEXT,
            metrik      TEXT
        );
        CREATE TABLE IF NOT EXISTS evalc_result (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT,
            metode        TEXT,
            intent        TEXT,
            pertanyaan    TEXT,
            history       TEXT,
            first_turn    INTEGER DEFAULT 1,
            with_history  INTEGER DEFAULT 0,
            session_id    TEXT,
            gold          TEXT,
            answer        TEXT,
            grounded      INTEGER DEFAULT 0,
            abstain       INTEGER DEFAULT 0,
            fallback_hit  INTEGER DEFAULT 0,
            latency_ms    REAL,
            sources       TEXT,
            domain        TEXT,
            judge_verdict TEXT,
            judge_skor    REAL,
            judge_alasan  TEXT,
            created_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ecr_run ON evalc_result(run_id);
        """
    )
    conn.commit()
    return conn


def _pct(sorted_list, p):
    if not sorted_list:
        return None
    k = (len(sorted_list) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_list[int(k)], 1)
    return round(sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f), 1)


def _profile(profil):
    p = rcfg.get_profile(profil) or rcfg.get_profile("chatbot")
    return dict(p) if p else {"id": "chatbot", "fallback": rcfg.FALLBACK_DEFAULT}


def _fallback_text(profile):
    return (profile.get("fallback") or rcfg.FALLBACK_DEFAULT)


# ---------------------------------------------------------------- filter sampel
# Token menu / navigasi / event yang BUKAN pertanyaan wajib pajak sungguhan.
_MENU_TOKENS = {
    "menu utama", "menu", "home", "kembali", "welcome", "mulai", "start",
    "ya", "tidak", "iya", "lanjut", "batal", "ok", "oke", "selesai",
    "test", "testing", "abc", "asdf", "qwerty", "coba",
}


def _looks_junk(phrase):
    """True bila frasa fallback jelas BUKAN pertanyaan nyata (perlu dibuang dari
    sampel Metode 2): fragmen HTML, kode chip menu (cbxNN), token navigasi,
    penanda testing, atau murni angka/kode (nomor tiket/telepon)."""
    p = (phrase or "").strip()
    if len(p) < 4:
        return True
    low = p.lower()
    if "<" in p or ">" in p or "style=" in low or "http://" in low or "https://" in low:
        return True
    if re.match(r"^cbx\d+$", low):
        return True
    if low in _MENU_TOKENS:
        return True
    if "testing" in low:
        return True
    if re.match(r"^[\d\s.,/#:+()-]+$", p):
        return True
    # harus ada minimal satu kata alfabet >= 3 huruf agar dianggap pertanyaan
    if not re.findall(r"[a-zA-Z\u00c0-\u024f]{3,}", p):
        return True
    return False


def _is_clean_turn(turn):
    """Turn 'bersih' = user menjawab & intent MATCH (bukan fallback, bukan
    System_/Umum_, bukan event WELCOME). HANYA turn seperti ini yang boleh
    dijadikan riwayat percakapan sebelumnya."""
    if turn.get("is_fallback"):
        return False
    it = (turn.get("intent") or "").strip()
    if not it or it.startswith("System_") or it.startswith("Umum_"):
        return False
    up = (turn.get("user_phrase") or "").strip()
    if not up or up.upper() == "WELCOME":
        return False
    return True


def _run_one(profile, question, history=None):
    t0 = time.time()
    try:
        res = rag_engine.answer(question, profile, override=None,
                                history=history, diagnostics=False)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "answer": "",
                "grounded": False, "abstain": True, "fallback_hit": True,
                "domain": "", "sources": [], "latency_ms": (time.time() - t0) * 1000.0}
    dt = (time.time() - t0) * 1000.0
    res = res or {}
    grounded = bool(res.get("grounded"))
    ans = res.get("answer") or ""
    fb = _fallback_text(profile)
    fallback_hit = (not grounded) or (bool(ans.strip()) and ans.strip() == (fb or "").strip())
    return {"ok": bool(res.get("ok", True)), "answer": ans, "grounded": grounded,
            "abstain": (not grounded), "fallback_hit": bool(fallback_hit),
            "domain": res.get("domain") or "", "sources": res.get("sources") or [],
            "latency_ms": dt}


# ---------------------------------------------------------------- sampling
def sample_intent_phrases(top_n=100, window="90d", lang=None, per_intent=12):
    conn = adb.init_db(adb.connect())
    try:
        if imdb is not None:
            try:
                imdb.init_catalog(conn)
            except Exception:
                pass
        start, end = adb.resolve_range(window)
        tops = adb.top_intents(conn, start, end, limit=int(top_n),
                               include_system=False, include_umum=False, lang=lang)
        out = []
        for t in tops:
            intent = (t.get("intent") or "").strip()
            if not intent or intent == "(kosong)":
                continue
            # Lewati intent uji-coba/testing yang bukan pengetahuan wajib pajak.
            il = intent.lower()
            if "testing" in il or il.startswith("test_") or il.endswith("_test"):
                continue
            row = conn.execute(
                "SELECT training_phrase_contoh, jawaban_cuplikan FROM intentmap_catalog WHERE intent=?",
                (intent,)).fetchone()
            phrases, gold = [], ""
            if row:
                try:
                    phrases = json.loads(row["training_phrase_contoh"] or "[]")
                except Exception:
                    phrases = []
                gold = row["jawaban_cuplikan"] or ""
            phrases = [str(p).strip() for p in phrases if str(p).strip()]
            # Buang frasa sampah (kode chip cbxNN, angka/kode murni, token menu,
            # penanda testing) agar coverage tidak dihukum oleh sampel non-pertanyaan.
            phrases = [ph for ph in phrases if not _looks_junk(ph)][: int(per_intent)]
            for ph in phrases:
                out.append({"intent": intent, "pertanyaan": ph, "gold": gold,
                            "first_turn": True, "history": [], "session_id": ""})
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _first_occurrence_context(conn, phrase, start, end, lang, max_history=6):
    """Rekonstruksi konteks percakapan untuk satu frasa fallback.

    Definisi 'riwayat' (sesuai kebutuhan): HANYA giliran user yang MATCH intent
    (bersih) + jawaban bot SEBELUM turn fallback ini. Turn WELCOME/system dan
    turn fallback lain TIDAK dihitung sebagai riwayat.

    first_turn=True  -> tidak ada giliran bersih sebelum fallback (mis. user
                        langsung fallback tepat setelah WELCOME). Diuji tanpa riwayat.
    first_turn=False -> ada >=1 giliran bersih sebelumnya (lanjutan). Diuji DENGAN riwayat.
    """
    target = adb._norm_phrase(phrase)
    try:
        det = adb.candidate_detail(conn, phrase, start, end, lang)
        sessions = det.get("sessions") or []
    except Exception:
        sessions = []
    if not sessions:
        return {"first_turn": True, "history": [], "session_id": ""}
    sessions_sorted = sorted(sessions, key=lambda s: (s.get("ts_first") or ""))
    sid = sessions_sorted[0].get("session_id") or ""
    try:
        tx = adb.session_transcript(conn, sid)
    except Exception:
        tx = []
    # Cari turn FALLBACK pertama yang cocok frasa target.
    idx = None
    for i, turn in enumerate(tx):
        if turn.get("is_fallback") and adb._norm_phrase(turn.get("user_phrase")) == target:
            idx = i
            break
    if idx is None:                       # cadangan: cocokkan tanpa syarat fallback
        for i, turn in enumerate(tx):
            if adb._norm_phrase(turn.get("user_phrase")) == target:
                idx = i
                break
    if idx is None:
        return {"first_turn": True, "history": [], "session_id": sid}
    # Susun riwayat HANYA dari giliran bersih (match) sebelum turn fallback ini.
    pairs = []
    for turn in tx[:idx]:
        if not _is_clean_turn(turn):
            continue
        up = (turn.get("user_phrase") or "").strip()
        br = (turn.get("bot_response") or "").strip()
        pairs.append((up, br))
    max_pairs = max(1, int(max_history) // 2)
    pairs = pairs[-max_pairs:]
    history = []
    for up, br in pairs:
        if up:
            history.append({"role": "user", "content": up})
        if br:
            history.append({"role": "assistant", "content": br})
    first_turn = (len(history) == 0)
    return {"first_turn": first_turn, "history": history, "session_id": sid}


def sample_fallback_questions(window="30d", min_count=2, limit=200, lang=None, max_history=6):
    conn = adb.init_db(adb.connect())
    try:
        start, end = adb.resolve_range(window)
        # Ambil lebih banyak kandidat mentah karena sebagian akan dibuang filter sampah.
        nqs = adb.new_questions(conn, start, end, limit=int(limit) * 6, min_len=3, lang=lang)
        nqs = [q for q in nqs if int(q.get("count") or 0) >= int(min_count)]
        nqs = [q for q in nqs if not _looks_junk(q.get("phrase"))][: int(limit)]
        out = []
        for q in nqs:
            phrase = q["phrase"]
            info = _first_occurrence_context(conn, phrase, start, end, lang, max_history)
            out.append({"intent": "", "pertanyaan": phrase, "gold": "",
                        "count": q.get("count"),
                        "first_turn": info["first_turn"],
                        "history": info["history"],
                        "session_id": info["session_id"]})
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def peak_hits(window_seconds=5, window="30d", lang=None):
    conn = adb.init_db(adb.connect())
    try:
        start, end = adb.resolve_range(window)
        where, params = adb._range_where(start, end)
        where = adb._lang_where(where, params, lang)
        ws = max(1, int(window_seconds))
        sql = ("SELECT MAX(cnt) AS mx, AVG(cnt) AS av, COUNT(*) AS nb FROM ("
               "SELECT CAST(strftime('%s', replace(substr(ts,1,19),'T',' ')) AS INTEGER)/? AS b, "
               "COUNT(*) AS cnt FROM interactions" + where + " GROUP BY b)")
        row = conn.execute(sql, [ws] + params).fetchone()
        mx = int(row["mx"] or 0)
        av = round(float(row["av"] or 0.0), 2)
        nb = int(row["nb"] or 0)
        return {"ok": True, "window_seconds": ws, "window": window,
                "max": mx, "avg": av, "buckets": nb, "suggestion": mx}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------- runs
def _create_run(rid, metode, profil, params, n_total):
    conn = init_db(connect())
    try:
        conn.execute(
            "INSERT INTO evalc_run(id,metode,profil,params,status,n_total,n_done,started_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (rid, metode, profil, json.dumps(params or {}, ensure_ascii=False),
             "running", int(n_total), 0, _now()))
        conn.commit()
    finally:
        conn.close()


def _judge_result(pertanyaan, gold, r):
    if eval_judge is None:
        return {}
    try:
        srcs = r.get("sources") or []
        stext = "\n".join("- %s %s" % (s.get("sumber", ""), s.get("judul", "")) for s in srcs)
        j = eval_judge.judge_one(pertanyaan, gold or "", r.get("answer") or "",
                                 bool(r.get("abstain")), stext)
        return j or {}
    except Exception as e:
        return {"verdict": "", "skor": None, "alasan": ("juri gagal: " + str(e)[:120])}


def _save_result(conn, run_id, metode, s, r, jr, with_hist):
    conn.execute(
        "INSERT INTO evalc_result(run_id,metode,intent,pertanyaan,history,first_turn,with_history,"
        "session_id,gold,answer,grounded,abstain,fallback_hit,latency_ms,sources,domain,"
        "judge_verdict,judge_skor,judge_alasan,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, metode, s.get("intent") or "", s.get("pertanyaan") or "",
         json.dumps(s.get("history") or [], ensure_ascii=False),
         int(bool(s.get("first_turn", True))), int(bool(with_hist)),
         s.get("session_id") or "", s.get("gold") or "", r.get("answer") or "",
         int(bool(r.get("grounded"))), int(bool(r.get("abstain"))), int(bool(r.get("fallback_hit"))),
         r.get("latency_ms"), json.dumps(r.get("sources") or [], ensure_ascii=False),
         r.get("domain") or "", (jr.get("verdict") or ""), jr.get("skor"),
         (jr.get("alasan") or ""), _now()))
    conn.commit()


def compute_metrics(conn, run_id, metode):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM evalc_result WHERE run_id=?", (run_id,)).fetchall()]
    total = len(rows)
    grounded = sum(1 for r in rows if r["grounded"])
    fallback = sum(1 for r in rows if r["fallback_hit"])
    answered = total - fallback
    # Coverage/deflection = benar-benar TERJAWAB (bukan fallback). Sebelumnya
    # dihitung dari flag 'grounded' yang bisa TRUE walau mesin mengeluarkan teks
    # fallback -> badge tak konsisten dengan fallback_rate. Pakai (total-fallback)
    # agar coverage + fallback_rate = 100%.
    m = {"total": total,
         "grounded": grounded,
         "answered": answered,
         "coverage": round(answered / total * 100.0, 2) if total else 0.0,
         "deflection": round(answered / total * 100.0, 2) if total else 0.0,
         "fallback_rate": round(fallback / total * 100.0, 2) if total else 0.0}
    verds = {}
    skor_sum, skor_n = 0.0, 0
    for r in rows:
        v = r.get("judge_verdict") or ""
        if v:
            verds[v] = verds.get(v, 0) + 1
        if r.get("judge_skor") is not None:
            skor_sum += float(r["judge_skor"]); skor_n += 1
    m["judge"] = verds
    m["skor_rata"] = round(skor_sum / skor_n, 3) if skor_n else None
    m["halusinasi_rate"] = round(verds.get("halusinasi", 0) / total * 100.0, 2) if total else 0.0
    # Presisi saat menjawab = benar / (benar+salah+halusinasi). Lebih jujur
    # daripada coverage karena menghukum jawaban yang keliru/dikarang.
    dijawab_juri = verds.get("benar", 0) + verds.get("salah", 0) + verds.get("halusinasi", 0)
    m["presisi_jawab"] = round(verds.get("benar", 0) / dijawab_juri * 100.0, 2) if dijawab_juri else None
    if metode == "chatbot_fallback":
        def dr(rr):
            t = len(rr); g = sum(1 for x in rr if not x["fallback_hit"])
            return {"total": t, "deflection": round(g / t * 100.0, 2) if t else 0.0}
        m["first_turn"] = dr([r for r in rows if r["first_turn"] and not r["with_history"]])
        m["followup_with_history"] = dr([r for r in rows if not r["first_turn"] and r["with_history"]])
        fu_n = [r for r in rows if not r["first_turn"] and not r["with_history"]]
        if fu_n:
            m["followup_no_history"] = dr(fu_n)
    if metode == "chatbot_intent":
        # Diagnosis penyebab fallback pada frasa yang SEHARUSNYA dikenali:
        #  - recall_miss          : intent asal frasa TIDAK ikut terambil retrieval.
        #  - retrieved_but_abstain: intent asal TERAMBIL, tapi mesin tetap abstain
        #                           (verifikasi/grounding terlalu ketat).
        miss = 0
        abst = 0
        for r in rows:
            if not r["fallback_hit"]:
                continue
            intent = (r.get("intent") or "").strip()
            try:
                srcs = json.loads(r.get("sources") or "[]")
            except Exception:
                srcs = []
            hay = " || ".join(str(s.get("judul", "")) for s in srcs if isinstance(s, dict))
            if intent and intent in hay:
                abst += 1
            else:
                miss += 1
        m["recall_miss"] = miss
        m["retrieved_but_abstain"] = abst
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    if lat:
        ls = sorted(lat)
        m["latency_ms"] = {"avg": round(sum(lat) / len(lat), 1),
                           "p50": _pct(ls, 50), "p95": _pct(ls, 95), "maks": round(max(lat), 1)}
    return m


def _worker_qa(run_id, metode, profil, samples, judge, also_no_history):
    conn = init_db(connect())
    profile = _profile(profil)
    done = 0
    try:
        for s in samples:
            with _LOCK:
                job = _JOBS.get(run_id)
            if job and job.get("stop"):
                break
            pertanyaan = s.get("pertanyaan") or ""
            gold = s.get("gold") or ""
            variants = []
            if metode == "chatbot_fallback":
                if s.get("first_turn"):
                    variants.append((False, []))
                else:
                    variants.append((True, s.get("history") or []))
                    if also_no_history:
                        variants.append((False, []))
            else:
                variants.append((False, None))
            for with_hist, hist in variants:
                r = _run_one(profile, pertanyaan, history=hist)
                jr = _judge_result(pertanyaan, gold, r) if (judge and r.get("ok")) else {}
                _save_result(conn, run_id, metode, s, r, jr, with_hist)
            done += 1
            conn.execute("UPDATE evalc_run SET n_done=? WHERE id=?", (done, run_id))
            conn.commit()
        metr = compute_metrics(conn, run_id, metode)
        conn.execute("UPDATE evalc_run SET status=?, finished_at=?, metrik=? WHERE id=?",
                     ("done", _now(), json.dumps(metr, ensure_ascii=False), run_id))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE evalc_run SET status=?, finished_at=?, note=? WHERE id=?",
                     ("error", _now(), str(e)[:300], run_id))
        conn.commit()
    finally:
        conn.close()
        with _LOCK:
            _JOBS.pop(run_id, None)


def start_intent(profil="chatbot", top_n=100, window="90d", lang=None,
                 per_intent=12, judge=True, limit=None):
    samples = sample_intent_phrases(top_n, window, lang, per_intent)
    if limit:
        try:
            samples = samples[: int(limit)]
        except Exception:
            pass
    if not samples:
        return {"ok": True, "run_id": None, "n_total": 0,
                "note": "Tidak ada training phrase pada katalog intent. Sinkronkan katalog di menu Peta Intent lebih dulu."}
    rid = "chatbot_intent_" + uuid.uuid4().hex[:10]
    _create_run(rid, "chatbot_intent", profil,
                {"top_n": top_n, "window": window, "per_intent": per_intent, "judge": bool(judge)},
                len(samples))
    with _LOCK:
        _JOBS[rid] = {"stop": False}
    threading.Thread(target=_worker_qa, args=(rid, "chatbot_intent", profil, samples,
                                              bool(judge), False), daemon=True).start()
    return {"ok": True, "run_id": rid, "n_total": len(samples)}


def start_fallback(profil="chatbot", window="30d", min_count=2, limit=200, lang=None,
                   judge=False, also_no_history=False):
    samples = sample_fallback_questions(window, min_count, limit, lang)
    if not samples:
        return {"ok": True, "run_id": None, "n_total": 0,
                "note": "Tidak ada pertanyaan fallback (>%d kali) pada rentang ini." % int(min_count)}
    rid = "chatbot_fallback_" + uuid.uuid4().hex[:10]
    _create_run(rid, "chatbot_fallback", profil,
                {"window": window, "min_count": min_count, "judge": bool(judge),
                 "also_no_history": bool(also_no_history)}, len(samples))
    with _LOCK:
        _JOBS[rid] = {"stop": False}
    threading.Thread(target=_worker_qa, args=(rid, "chatbot_fallback", profil, samples,
                                              bool(judge), bool(also_no_history)), daemon=True).start()
    return {"ok": True, "run_id": rid, "n_total": len(samples)}


def _worker_load(run_id, profile, concurrency, total, question):
    conn = init_db(connect())
    results = []
    done = {"n": 0}
    rlock = threading.Lock()

    def one(_i):
        t0 = time.time()
        ok, err = True, ""
        try:
            res = rag_engine.answer(question, profile, override=None,
                                    history=None, diagnostics=False)
            ok = bool((res or {}).get("ok", True))
        except Exception as e:
            ok, err = False, str(e)[:150]
        dt = (time.time() - t0) * 1000.0
        with rlock:
            results.append({"ms": dt, "ok": ok, "err": err})
            done["n"] += 1

    t_start = time.time()
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as ex:
            futs = [ex.submit(one, i) for i in range(int(total))]
            last = 0
            for _f in as_completed(futs):
                n = done["n"]
                if n - last >= 1:
                    conn.execute("UPDATE evalc_run SET n_done=? WHERE id=?", (n, run_id))
                    conn.commit()
                    last = n
        wall = time.time() - t_start
        lat = [r["ms"] for r in results]
        ls = sorted(lat)
        errs = sum(1 for r in results if not r["ok"])
        n = len(results)
        metr = {"total": n, "concurrency": int(concurrency),
                "wall_s": round(wall, 2),
                "throughput_rps": round(n / wall, 2) if wall > 0 else None,
                "error": errs,
                "error_rate": round(errs / n * 100.0, 2) if n else 0.0,
                "latency_ms": {"avg": round(sum(lat) / len(lat), 1) if lat else None,
                               "p50": _pct(ls, 50), "p95": _pct(ls, 95),
                               "p99": _pct(ls, 99), "maks": round(max(lat), 1) if lat else None,
                               "min": round(min(lat), 1) if lat else None}}
        conn.execute("UPDATE evalc_run SET status=?, finished_at=?, n_done=?, metrik=? WHERE id=?",
                     ("done", _now(), n, json.dumps(metr, ensure_ascii=False), run_id))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE evalc_run SET status=?, finished_at=?, note=? WHERE id=?",
                     ("error", _now(), str(e)[:300], run_id))
        conn.commit()
    finally:
        conn.close()
        with _LOCK:
            _JOBS.pop(run_id, None)


def start_load(profil="chatbot", mode="tanpa_llm", concurrency=20, total=100, question=None):
    profile = _profile(profil)
    mode = (mode or "").strip().lower()
    if mode in ("tanpa_llm", "llm", "full", ""):
        profile["mode"] = mode
    concurrency = max(1, min(int(concurrency or 1), 200))
    total = max(1, min(int(total or 1), 1000))
    q = (question or "").strip() or "bagaimana cara lapor SPT tahunan?"
    rid = "chatbot_load_" + uuid.uuid4().hex[:10]
    _create_run(rid, "chatbot_load", profil,
                {"mode": mode, "concurrency": concurrency, "total": total, "question": q}, total)
    with _LOCK:
        _JOBS[rid] = {"stop": False}
    threading.Thread(target=_worker_load, args=(rid, profile, concurrency, total, q),
                     daemon=True).start()
    return {"ok": True, "run_id": rid, "n_total": total}


def stop(run_id):
    with _LOCK:
        j = _JOBS.get(run_id)
        if j:
            j["stop"] = True
    return {"ok": True, "run_id": run_id}


def _decode_run(d):
    for k in ("params", "metrik"):
        try:
            d[k] = json.loads(d.get(k) or "null")
        except Exception:
            d[k] = None
    return d


def status(run_id):
    conn = init_db(connect())
    try:
        r = conn.execute("SELECT * FROM evalc_run WHERE id=?", (run_id,)).fetchone()
        if not r:
            return {"ok": False, "error": "run tidak ditemukan"}
        return {"ok": True, "run": _decode_run(dict(r))}
    finally:
        conn.close()


def list_runs(limit=20, metode=None):
    conn = init_db(connect())
    try:
        q = "SELECT id,metode,profil,status,n_total,n_done,started_at,finished_at FROM evalc_run"
        params = []
        if metode:
            q += " WHERE metode=?"; params.append(metode)
        q += " ORDER BY started_at DESC LIMIT ?"; params.append(int(limit))
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


def report(run_id=None, metode=None, only=None):
    conn = init_db(connect())
    try:
        if not run_id:
            q = "SELECT * FROM evalc_run WHERE status='done'"
            params = []
            if metode:
                q += " AND metode=?"; params.append(metode)
            q += " ORDER BY started_at DESC LIMIT 1"
            r = conn.execute(q, params).fetchone()
            run_id = r["id"] if r else None
        if not run_id:
            return {"ok": True, "run": None, "metrik": None, "results": []}
        rr = conn.execute("SELECT * FROM evalc_run WHERE id=?", (run_id,)).fetchone()
        if not rr:
            return {"ok": True, "run": None, "metrik": None, "results": []}
        run = _decode_run(dict(rr))
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM evalc_result WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]
        for d in rows:
            try:
                d["sources"] = json.loads(d.get("sources") or "[]")
            except Exception:
                d["sources"] = []
            try:
                d["history"] = json.loads(d.get("history") or "[]")
            except Exception:
                d["history"] = []
            # Diagnosis Metode 1: apakah intent asal frasa ikut terambil retrieval?
            hay = " || ".join(str(s.get("judul", "")) for s in d["sources"] if isinstance(s, dict))
            d["self_retrieved"] = bool(d.get("intent")) and ((d.get("intent") or "") in hay)
        if only == "fallback":
            rows = [d for d in rows if d.get("fallback_hit")]
        elif only in ("hallucination", "halusinasi"):
            rows = [d for d in rows if d.get("judge_verdict") == "halusinasi"]
        elif only == "salah":
            rows = [d for d in rows if d.get("judge_verdict") == "salah"]
        elif only == "abstain_salah":
            rows = [d for d in rows if d.get("judge_verdict") == "abstain_salah"]
        return {"ok": True, "run": run, "metrik": run.get("metrik"), "results": rows[:200]}
    finally:
        conn.close()
