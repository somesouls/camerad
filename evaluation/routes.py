# -*- coding: utf-8 -*-
"""eval_routes.py — Menu \"Evaluasi RAG\": kumpulkan sampel, jalankan uji,
dashboard keandalan, dan validasi manusia.

Rute (area akses 'peraturan' = admin; lihat app_core._route_area):
  GET  /rag-eval                 -> halaman menu
  GET  /api/eval/summary         -> ringkasan sampel + daftar run + profil
  POST /api/eval/collect         -> kumpulkan sampel {jenis, n_live, n_chat, reset}
  POST /api/eval/run             -> mulai evaluasi {profil, jenis, limit, holdout, judge}
  GET  /api/eval/status?run=..   -> progres run
  GET  /api/eval/report?run=..   -> metrik + daftar hasil (opsi only=fail)
  POST /api/eval/stop            -> hentikan run {run}
  POST /api/eval/human           -> simpan validasi manusia {result_id, verdict, note}
  POST /api/eval/sweep           -> mulai kalibrasi sweep {profil,jenis,limit,holdout,judge,thresholds}
  GET  /api/eval/sweep/status    -> progres sweep + metrik per ambang + rekomendasi {sweep}
  POST /api/eval/sweep/stop      -> hentikan sweep {sweep}

Daftarkan dengan:  import eval_routes; eval_routes.register(app)
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import evaluation.db as eval_db
import evaluation.sampler as eval_sampler
import evaluation.harness as eval_harness
import evaluation.sweep as eval_sweep
import rag.config_db as rcfg


async def _body(request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        d = json.loads(raw.decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def page_eval(request: Request):
    return render_page(request, "eval.html", "rag_eval", {
        "profil_list": rcfg.list_profiles(),
        "verdicts": list(eval_db.VERDICTS),
    })


async def api_summary(request: Request):
    conn = eval_db.init_db(eval_db.connect())
    try:
        return JSONResponse({"ok": True,
                             "counts": eval_db.sample_counts(conn),
                             "runs": eval_db.list_runs(conn, 15),
                             "profil": rcfg.list_profiles()})
    finally:
        conn.close()


async def api_collect(request: Request):
    b = await _body(request)
    jenis = (b.get("jenis") or "all").strip()
    n_live = int(b.get("n_live") or 300)
    n_chat = int(b.get("n_chat") or 200)
    reset = bool(b.get("reset"))
    if reset:
        conn = eval_db.init_db(eval_db.connect())
        eval_db.clear_samples(conn, jenis if jenis != "all" else None)
        conn.close()
    try:
        if jenis == "livechat":
            r = await run_in_threadpool(eval_sampler.collect_livechat, n_live)
        elif jenis == "chatbot":
            r = await run_in_threadpool(eval_sampler.collect_chatbot, n_chat)
        else:
            r = await run_in_threadpool(eval_sampler.collect_all, n_live, n_chat)
        return JSONResponse({"ok": True, "hasil": r})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_run(request: Request):
    b = await _body(request)
    profil = (b.get("profil") or "agent").strip()
    jenis = (b.get("jenis") or "all").strip()
    limit = b.get("limit")
    try:
        limit = int(limit) if limit not in (None, "", 0, "0") else None
    except Exception:
        limit = None
    holdout = b.get("holdout", True)
    judge = b.get("judge", True)
    try:
        r = await run_in_threadpool(eval_harness.start_eval, profil, jenis, limit,
                                    bool(holdout), bool(judge))
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_status(request: Request):
    run_id = request.query_params.get("run") or None
    conn = eval_db.init_db(eval_db.connect())
    try:
        run = eval_db.get_run(conn, run_id) if run_id else None
        return JSONResponse({"ok": True, "run": run})
    finally:
        conn.close()


async def api_report(request: Request):
    run_id = request.query_params.get("run") or None
    only = request.query_params.get("only") or None
    conn = eval_db.init_db(eval_db.connect())
    try:
        if not run_id:
            r = eval_db.latest_run(conn, "done")
            run_id = r["id"] if r else None
        if not run_id:
            return JSONResponse({"ok": True, "run": None, "metrics": None, "results": []})
        return JSONResponse({"ok": True, "run": eval_db.get_run(conn, run_id),
                             "metrics": eval_db.metrics(conn, run_id),
                             "results": eval_db.list_results(conn, run_id, only=only)})
    finally:
        conn.close()


async def api_stop(request: Request):
    b = await _body(request)
    return JSONResponse(eval_harness.stop_eval((b.get("run") or "").strip()))


async def api_human(request: Request):
    b = await _body(request)
    conn = eval_db.init_db(eval_db.connect())
    try:
        return JSONResponse(eval_db.set_human_verdict(
            conn, int(b.get("result_id")),
            (b.get("verdict") or "").strip(), (b.get("note") or "").strip()))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    finally:
        conn.close()


def _parse_thresholds(val):
    """Terima list angka, atau string dgn pemisah '/' ';' spasi; koma = desimal.
    Contoh: \"0,30/0,35/0,40\" -> [0.30, 0.35, 0.40]. None bila kosong.
    """
    if isinstance(val, (list, tuple)):
        out = []
        for x in val:
            try:
                out.append(float(x))
            except Exception:
                pass
        return out or None
    if isinstance(val, str):
        raw = (val.replace(";", "/").replace(" ", "/")
                  .replace("\n", "/").replace("\t", "/"))
        out = []
        for p in raw.split("/"):
            p = p.strip().replace(",", ".")
            if not p:
                continue
            try:
                out.append(float(p))
            except Exception:
                pass
        return out or None
    return None


async def api_sweep(request: Request):
    b = await _body(request)
    profil = (b.get("profil") or "agent").strip()
    jenis = (b.get("jenis") or "all").strip()
    limit = b.get("limit")
    try:
        limit = int(limit) if limit not in (None, "", 0, "0") else None
    except Exception:
        limit = None
    holdout = bool(b.get("holdout", True))
    judge = bool(b.get("judge", True))
    thresholds = _parse_thresholds(b.get("thresholds"))
    try:
        r = await run_in_threadpool(eval_sweep.start_sweep, profil, jenis, limit,
                                    holdout, judge, thresholds)
        return JSONResponse(r)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_sweep_status(request: Request):
    sweep_id = (request.query_params.get("sweep") or "").strip()
    if not sweep_id:
        return JSONResponse({"ok": False, "error": "parameter sweep kosong"})
    try:
        return JSONResponse(await run_in_threadpool(eval_sweep.sweep_status, sweep_id))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_sweep_stop(request: Request):
    b = await _body(request)
    return JSONResponse(eval_sweep.stop_sweep((b.get("sweep") or "").strip()))


def register(app):
    app.add_api_route("/rag-eval", page_eval, methods=["GET"])
    app.add_api_route("/api/eval/summary", api_summary, methods=["GET"])
    app.add_api_route("/api/eval/collect", api_collect, methods=["POST"])
    app.add_api_route("/api/eval/run", api_run, methods=["POST"])
    app.add_api_route("/api/eval/status", api_status, methods=["GET"])
    app.add_api_route("/api/eval/report", api_report, methods=["GET"])
    app.add_api_route("/api/eval/stop", api_stop, methods=["POST"])
    app.add_api_route("/api/eval/human", api_human, methods=["POST"])
    app.add_api_route("/api/eval/sweep", api_sweep, methods=["POST"])
    app.add_api_route("/api/eval/sweep/status", api_sweep_status, methods=["GET"])
    app.add_api_route("/api/eval/sweep/stop", api_sweep_stop, methods=["POST"])
