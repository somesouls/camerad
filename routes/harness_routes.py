# -*- coding: utf-8 -*-
"""harness_routes.py — Backend panel admin \"RAG Harness\" (Tahap 4 #1).

Menu baru mandiri /rag-harness (4 tab: Golden · Gerbang eval · Tambang feedback ·
Knob per-profil). LANGKAH 3a = HANYA backend READ-ONLY: endpoint GET yang
menyajikan (a) knob efektif per-profil, (b) ringkasan golden set, (c) baseline
gerbang, (d) laporan eval terbaru dari _runs. Aksi tulis (jalankan eval, tambang
feedback, set knob, CRUD golden) menyusul di langkah berikut, dengan konfirmasi.

Helper di bawah MURNI (tanpa FastAPI) supaya bisa diuji lewat CLI:
    python -m routes.harness_routes --section overview --profile agent
    python -m routes.harness_routes --section knobs   --profile chatbot
    python -m routes.harness_routes --section golden
    python -m routes.harness_routes --section baseline
    python -m routes.harness_routes --section runs --limit 3

Daftarkan (langkah 3b): import routes.harness_routes as h; h.register(app)
Area admin (langkah 3c): tambah '/rag-harness' & '/api/harness' ke _route_area
di app_core (area 'peraturan'). Stdlib + FastAPI; tanpa f-string; GAGAL-ANGGUN.
"""
import os
import sys
import json
import glob
import argparse

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

try:
    import rag.knob_store as ks
except Exception:            # pragma: no cover
    ks = None
try:
    import rag.golden_db as gdb
except Exception:            # pragma: no cover
    gdb = None

_PROFILES = ("agent", "chatbot")


def _norm_profile(p):
    v = (p or "").strip().lower()
    return v if v in _PROFILES else "agent"


# ------------------------------------------------------------ helper MURNI
def knobs_overview(profile="agent"):
    """Knob efektif (value/source/default/label) utk satu profil."""
    prof = _norm_profile(profile)
    out = {"profile": prof, "profiles": list(_PROFILES), "knobs": {}}
    if ks is None:
        out["error"] = "knob_store tak tersedia"
        return out
    try:
        out["knobs"] = ks.all_effective(prof)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def golden_overview(include_rows=True):
    """Ringkasan golden set: jumlah per-jenis + (opsional) daftar entri."""
    out = {"total": 0, "n_hit": 0, "n_abstain": 0, "n_aktif": 0, "rows": []}
    if gdb is None:
        out["error"] = "golden_db tak tersedia"
        return out
    try:
        rows = gdb.list_golden()
        out["total"] = len(rows)
        for r in rows:
            jh = r.get("jenis_harapan")
            if jh == "hit":
                out["n_hit"] += 1
            elif jh == "abstain":
                out["n_abstain"] += 1
            if r.get("aktif"):
                out["n_aktif"] += 1
            if include_rows:
                out["rows"].append({
                    "id": r.get("id"),
                    "query": r.get("query"),
                    "jenis_harapan": jh,
                    "aktif": bool(r.get("aktif")),
                    "expect": r.get("expect") or {},
                    "catatan": r.get("catatan") or "",
                })
    except Exception as e:
        out["error"] = str(e)[:200]
    if not include_rows:
        out.pop("rows", None)
    return out


def _baseline_path(path=None):
    if path:
        return path if os.path.isabs(path) else os.path.join(_BASE_DIR, path)
    return os.path.join(_BASE_DIR, "golden_base.json")


def baseline_overview(path=None):
    """Isi baseline gerbang (golden_base.json) apa adanya."""
    fp = _baseline_path(path)
    out = {"path": fp, "exists": os.path.exists(fp)}
    if not out["exists"]:
        return out
    try:
        fh = open(fp, "r", encoding="utf-8")
        try:
            out["baseline"] = json.load(fh)
        finally:
            fh.close()
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def _runs_dir():
    return os.environ.get("PIPELINE_RUNS_DIR") or os.path.join(_BASE_DIR, "_runs")


def latest_runs(limit=1):
    """Laporan eval terbaru (golden_eval_*.json) dari _runs, terbaru dulu."""
    rd = _runs_dir()
    out = {"runs_dir": rd, "runs": []}
    try:
        files = glob.glob(os.path.join(rd, "golden_eval_*.json"))
        files.sort(reverse=True)
    except Exception as e:
        out["error"] = str(e)[:200]
        return out
    try:
        n = int(limit or 1)
    except Exception:
        n = 1
    if n < 1:
        n = 1
    for fp in files[:n]:
        item = {"file": os.path.basename(fp)}
        try:
            fh = open(fp, "r", encoding="utf-8")
            try:
                item["report"] = json.load(fh)
            finally:
                fh.close()
        except Exception as e:
            item["error"] = str(e)[:160]
        out["runs"].append(item)
    return out


def overview(profile="agent"):
    """Ringkasan gabungan untuk halaman /rag-harness (read-only)."""
    return {
        "ok": True,
        "knobs": knobs_overview(profile),
        "golden": golden_overview(include_rows=False),
        "baseline": baseline_overview(),
        "latest_run": latest_runs(1),
    }


# ------------------------------------------------------------ route FastAPI
try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
except Exception:            # pragma: no cover
    Request = object
    JSONResponse = None
    run_in_threadpool = None


def register(app):
    """Daftarkan endpoint GET read-only. Dipanggil dari web_app (langkah 3b)."""
    async def api_overview(request: Request):
        prof = request.query_params.get("profile") if hasattr(request, "query_params") else "agent"
        try:
            return JSONResponse(await run_in_threadpool(overview, prof))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_knobs(request: Request):
        prof = request.query_params.get("profile")
        try:
            res = await run_in_threadpool(knobs_overview, prof)
            return JSONResponse({"ok": True, **res})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden(request: Request):
        try:
            res = await run_in_threadpool(golden_overview, True)
            return JSONResponse({"ok": True, **res})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_baseline(request: Request):
        try:
            res = await run_in_threadpool(baseline_overview, None)
            return JSONResponse({"ok": True, **res})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_runs(request: Request):
        lim = request.query_params.get("limit") or "1"
        try:
            res = await run_in_threadpool(latest_runs, lim)
            return JSONResponse({"ok": True, **res})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    app.add_api_route("/api/harness/overview", api_overview, methods=["GET"])
    app.add_api_route("/api/harness/knobs", api_knobs, methods=["GET"])
    app.add_api_route("/api/harness/golden", api_golden, methods=["GET"])
    app.add_api_route("/api/harness/baseline", api_baseline, methods=["GET"])
    app.add_api_route("/api/harness/runs", api_runs, methods=["GET"])


# ------------------------------------------------------------ CLI uji cepat
def _cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Uji read-only backend RAG Harness (Tahap 4 #1).")
    ap.add_argument("--section", default="overview",
                    help="overview|knobs|golden|baseline|runs")
    ap.add_argument("--profile", default="agent", help="agent|chatbot")
    ap.add_argument("--limit", default=1, type=int)
    args = ap.parse_args(argv)

    sec = (args.section or "overview").strip().lower()
    if sec == "knobs":
        data = knobs_overview(args.profile)
    elif sec == "golden":
        data = golden_overview(include_rows=True)
    elif sec == "baseline":
        data = baseline_overview()
    elif sec == "runs":
        data = latest_runs(args.limit)
    else:
        data = overview(args.profile)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
