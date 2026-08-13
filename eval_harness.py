# -*- coding: utf-8 -*-
"""eval_harness.py — Jalankan evaluasi keandalan RAG atas sampel tersimpan.

Alur per sampel:
  1. Panggil mesin RAG (rag_engine.answer) dengan profil terpilih.
     - HOLD-OUT: untuk sampel LIVECHAT, sumber 'awe' dikecualikan agar mesin
       tidak \"mencontek\" balasan agen yang jadi gold (anti-kebocoran). Sumber
       chatbot (intent) memakai katalog terkurasi, bukan frasa mentah, jadi
       tak perlu hold-out.
  2. Tentukan status abstain (grounded=False atau jawaban == fallback).
  3. Nilai dengan eval_judge (LLM-as-judge).
  4. Simpan hasil ke eval_db + perbarui progres run.

Dijalankan di thread latar (LLM lambat). Progres dipantau via eval_db.get_run.
"""
import uuid
import threading
import traceback

import eval_db
import eval_judge
import rag_engine
import rag_config_db as rcfg
import rag_calibration as _cal

_LOCK = threading.Lock()
_JOBS = {}   # run_id -> {"stop": bool, "thread": Thread}


def _fallback_text(profile):
    return (profile.get("fallback") or rcfg.FALLBACK_DEFAULT or "").strip()


def _sources_txt(sources):
    out = []
    for s in sources or []:
        line = "- [%s] %s" % (s.get("sumber", ""), s.get("judul", ""))
        if s.get("ref"):
            line += " (%s)" % s.get("ref")
        out.append(line)
    return "\n".join(out)


def run_samples(run_id, profil, jenis, limit, holdout, judge, min_cos=None):
    """Inti SINKRON evaluasi satu run (dipakai thread _run & eval_sweep).

    min_cos: ambang cosine aktif utk run ini (None = default env RAG_MIN_COS).
    Di-set ke rag_calibration agar gerbang retrieval (rag_calibration_patch)
    menyaring sesuai ambang selama run berjalan di thread ini.
    """
    with _LOCK:
        _JOBS.setdefault(run_id, {"stop": False})
    conn = eval_db.init_db(eval_db.connect())
    profile = rcfg.get_profile(profil) or rcfg.get_profile("chatbot") or {}
    base_sumber = [s for s in (profile.get("sumber") or list(rcfg.SUMBER_VALID))
                   if s in rcfg.SUMBER_VALID] or list(rcfg.SUMBER_VALID)
    fb = _fallback_text(profile)
    try:
        _cal.set_min_cos(min_cos)
        samples = eval_db.list_samples(conn, jenis if jenis != "all" else None, limit=limit)
        n = 0
        for smp in samples:
            with _LOCK:
                job = _JOBS.get(run_id)
            if job and job.get("stop"):
                eval_db.finish_run(conn, run_id, "stopped", "dihentikan pengguna")
                conn.close()
                return
            is_live = smp.get("jenis") == "livechat"
            if holdout and is_live:
                override = [s for s in base_sumber if s != "awe"]
            else:
                override = None
            try:
                res = rag_engine.answer(smp["pertanyaan"], profile, override=override,
                                        history=None, diagnostics=True)
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            answer = (res.get("answer") or "").strip()
            grounded = bool(res.get("grounded"))
            abstain = (not grounded) or bool(
                fb and answer and answer[:60].lower() == fb[:60].lower())
            sources = res.get("sources") or []
            domain = res.get("domain") or ""
            rec = {"sample_id": smp["id"], "jenis": smp["jenis"], "profil": profil,
                   "pertanyaan": smp["pertanyaan"], "gold": smp.get("gold"),
                   "answer": answer, "grounded": grounded, "abstain": abstain,
                   "sources": sources, "domain": domain}
            if judge:
                jr = eval_judge.judge_one(smp["pertanyaan"], smp.get("gold"), answer,
                                          abstain, _sources_txt(sources))
                rec["judge_verdict"] = jr.get("verdict")
                rec["judge_grounded"] = jr.get("grounded")
                rec["judge_skor"] = jr.get("skor")
                rec["judge_alasan"] = jr.get("alasan")
            eval_db.save_result(conn, run_id, rec)
            n += 1
            eval_db.bump_run(conn, run_id, n)
        eval_db.finish_run(conn, run_id, "done", "")
    except Exception as e:
        eval_db.finish_run(conn, run_id, "error",
                           (str(e) + " | " + traceback.format_exc())[:400])
    finally:
        _cal.reset_min_cos()
        try:
            conn.close()
        except Exception:
            pass
        with _LOCK:
            _JOBS.pop(run_id, None)


def _run(run_id, profil, jenis, limit, holdout, judge):
    run_samples(run_id, profil, jenis, limit, holdout, judge, min_cos=None)


def start_eval(profil="agent", jenis="all", limit=None, holdout=True, judge=True):
    conn = eval_db.init_db(eval_db.connect())
    samples = eval_db.list_samples(conn, jenis if jenis != "all" else None, limit=limit)
    n_total = len(samples)
    run_id = uuid.uuid4().hex[:16]
    eval_db.create_run(conn, run_id, profil, jenis,
                       {"holdout": holdout, "judge": judge, "limit": limit}, n_total)
    conn.close()
    if n_total == 0:
        c2 = eval_db.connect()
        eval_db.finish_run(c2, run_id, "done", "tak ada sampel")
        c2.close()
        return {"ok": True, "run_id": run_id, "n_total": 0,
                "note": "Belum ada sampel. Kumpulkan sampel dulu."}
    t = threading.Thread(target=_run, args=(run_id, profil, jenis, limit, holdout, judge),
                         daemon=True)
    with _LOCK:
        _JOBS[run_id] = {"stop": False, "thread": t}
    t.start()
    return {"ok": True, "run_id": run_id, "n_total": n_total}


def stop_eval(run_id):
    with _LOCK:
        job = _JOBS.get(run_id)
        if job:
            job["stop"] = True
    return {"ok": True}
