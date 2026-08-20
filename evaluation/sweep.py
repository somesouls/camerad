# -*- coding: utf-8 -*-
"""eval_sweep.py — Kalibrasi otomatis: sapu beberapa ambang cosine & bandingkan.

Menjalankan baseline (sampel /rag-eval yang SAMA) berkali-kali, satu kali per
nilai ambang cosine, lalu menyajikan metrik per ambang + rekomendasi nilai
optimal (halusinasi < 3% dengan keandalan tertinggi).

Tiap ambang menjadi satu eval_run (ditandai sweep_id + min_cos). Dijalankan
BERURUTAN dalam satu thread latar agar tidak membebani LLM secara paralel.
"""
import uuid
import threading
import traceback

import evaluation.db as eval_db
import evaluation.harness as eval_harness

_LOCK = threading.Lock()
_JOBS = {}   # sweep_id -> {"stop": bool}

DEFAULT_THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55]


def _clean_thresholds(thresholds):
    out = []
    for t in (thresholds or []):
        try:
            v = round(float(t), 4)
        except Exception:
            continue
        if 0.0 <= v <= 1.0 and v not in out:
            out.append(v)
    out.sort()
    return out or list(DEFAULT_THRESHOLDS)


def _run_sweep(sweep_id, profil, jenis, limit, holdout, judge, thresholds, n_total):
    conn = eval_db.init_db(eval_db.connect())
    try:
        for mc in thresholds:
            with _LOCK:
                job = _JOBS.get(sweep_id)
            if job and job.get("stop"):
                break
            run_id = uuid.uuid4().hex[:16]
            eval_db.create_run(
                conn, run_id, profil, jenis,
                {"holdout": holdout, "judge": judge, "limit": limit,
                 "sweep_id": sweep_id, "min_cos": mc},
                n_total, sweep_id=sweep_id, min_cos=mc)
            try:
                eval_harness.run_samples(run_id, profil, jenis, limit,
                                         holdout, judge, min_cos=mc)
            except Exception as e:
                eval_db.finish_run(conn, run_id, "error",
                                   (str(e) + " | " + traceback.format_exc())[:400])
    finally:
        try:
            conn.close()
        except Exception:
            pass
        with _LOCK:
            _JOBS.pop(sweep_id, None)


def start_sweep(profil="agent", jenis="all", limit=None, holdout=True,
                judge=True, thresholds=None):
    thresholds = _clean_thresholds(thresholds)
    conn = eval_db.init_db(eval_db.connect())
    try:
        n_total = len(eval_db.list_samples(
            conn, jenis if jenis != "all" else None, limit=limit))
    finally:
        conn.close()
    sweep_id = "sw_" + uuid.uuid4().hex[:12]
    if n_total == 0:
        return {"ok": True, "sweep_id": sweep_id, "thresholds": thresholds,
                "n_total": 0, "note": "Belum ada sampel. Kumpulkan sampel dulu."}
    t = threading.Thread(
        target=_run_sweep,
        args=(sweep_id, profil, jenis, limit, holdout, judge, thresholds, n_total),
        daemon=True)
    with _LOCK:
        _JOBS[sweep_id] = {"stop": False}
    t.start()
    return {"ok": True, "sweep_id": sweep_id, "thresholds": thresholds,
            "n_total": n_total, "n_runs": len(thresholds)}


def stop_sweep(sweep_id):
    with _LOCK:
        job = _JOBS.get(sweep_id)
        if job:
            job["stop"] = True
    conn = eval_db.init_db(eval_db.connect())
    try:
        for r in eval_db.list_sweep(conn, sweep_id):
            if r.get("status") == "running":
                eval_harness.stop_eval(r["id"])
    finally:
        conn.close()
    return {"ok": True}


def _recommend(rows):
    """Pilih ambang: halusinasi < 3% dengan keandalan tertinggi; bila tak ada
    yang lolos gerbang halusinasi, pilih halusinasi terendah lalu keandalan
    tertinggi."""
    cand = []
    for r in rows:
        m = r.get("metrics") or {}
        if m.get("total"):
            cand.append((r.get("min_cos"),
                         float(m.get("keandalan") or 0.0),
                         float(m.get("halusinasi_rate") if m.get("halusinasi_rate") is not None else 100.0)))
    if not cand:
        return None
    lolos = [c for c in cand if c[2] < 3.0]
    if lolos:
        best = sorted(lolos, key=lambda c: (-c[1], c[2], c[0]))[0]
    else:
        best = sorted(cand, key=lambda c: (c[2], -c[1], c[0]))[0]
    return {"min_cos": best[0], "keandalan": best[1], "halusinasi_rate": best[2],
            "lolos_gerbang": bool(lolos)}


def sweep_status(sweep_id):
    conn = eval_db.init_db(eval_db.connect())
    try:
        runs = eval_db.list_sweep(conn, sweep_id)
        rows, done = [], 0
        for r in runs:
            m = eval_db.metrics(conn, r["id"])
            rows.append({"run_id": r["id"], "min_cos": r.get("min_cos"),
                         "status": r.get("status"), "n_done": r.get("n_done"),
                         "n_total": r.get("n_total"), "metrics": m})
            if r.get("status") in ("done", "stopped", "error"):
                done += 1
        with _LOCK:
            active = sweep_id in _JOBS
        return {"ok": True, "sweep_id": sweep_id, "rows": rows,
                "selesai": done, "total_run": len(runs),
                "berjalan": active, "rekomendasi": _recommend(rows)}
    finally:
        conn.close()
