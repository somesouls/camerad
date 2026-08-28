# -*- coding: utf-8 -*-
"""harness_routes.py — Backend panel admin \"RAG Harness\" (Tahap 4 #1).

Menu baru mandiri /rag-harness (4 tab: Golden · Gerbang eval · Tambang feedback ·
Knob per-profil). LANGKAH 3a = HANYA backend READ-ONLY: endpoint GET yang
menyajikan (a) knob efektif per-profil, (b) ringkasan golden set, (c) baseline
gerbang, (d) laporan eval terbaru dari _runs.

LANGKAH 4b menambah AKSI TULIS KNOB per-profil (POST /api/harness/knob/set &
/api/harness/knob/clear). Menulis knob HANYA mengisi knob store; pipeline runtime
BELUM membacanya (penyambungan = langkah 4e), jadi aksi ini INERT terhadap
perilaku produksi & aman diuji.

LANGKAH 4c menambah AKSI TULIS GOLDEN SET (POST /api/harness/golden/{upsert,
aktif,delete,seed,mirror}) via rag.golden_db. upsert/aktif/seed/mirror aman;
delete DESTRUKTIF (lebih aman set aktif=false utk mengeluarkan dari eval).
Mengubah golden set memengaruhi GERBANG eval, bukan runtime retrieval. Semua
tetap area admin 'peraturan' + GAGAL-ANGGUN.

LANGKAH 4d menambah AKSI JALANKAN EVAL & TAMBANG FEEDBACK. POST
/api/harness/eval/run menjalankan phase4_eval.py sebagai SUB-PROSES TERISOLASI
(baseline save/check opsional). Isolasi ini disengaja: phase4_eval menyetel env
global (RAG_REWRITE_AI=0, CUBLAS_WORKSPACE_CONFIG) saat impor dan
_setup_determinism() memaksa torch.use_deterministic_algorithms(True) +
cudnn.deterministic utk SELURUH proses — bila in-process itu mencemari server
produksi. GET /api/harness/mine menyajikan kandidat golden dari feedback
produksi (mine_feedback, read-only). Keduanya area admin 'peraturan' +
GAGAL-ANGGUN; INERT terhadap runtime retrieval.

Helper di bawah MURNI (tanpa FastAPI) supaya bisa diuji lewat CLI:
    python -m routes.harness_routes --section overview --profile agent
    python -m routes.harness_routes --section knobs   --profile chatbot
    python -m routes.harness_routes --section golden
    python -m routes.harness_routes --section baseline
    python -m routes.harness_routes --section runs --limit 3
    python -m routes.harness_routes --profile agent --set RAG_MIN_COS --value 0.61
    python -m routes.harness_routes --profile agent --clear RAG_MIN_COS
    python -m routes.harness_routes --golden-seed
    python -m routes.harness_routes --golden-upsert "contoh query" --jenis hit --expect '{"keywords":["pkp"]}'
    python -m routes.harness_routes --golden-aktif <ID> --aktif false
    python -m routes.harness_routes --golden-delete <ID>
    python -m routes.harness_routes --golden-mirror
    python -m routes.harness_routes --mine --limit 50
    python -m routes.harness_routes --eval-run --k 10
    python -m routes.harness_routes --eval-run --k 10 --eval-baseline-check

Daftarkan (langkah 3b): import routes.harness_routes as h; h.register(app)
Area admin (langkah 3c): tambah '/rag-harness' & '/api/harness' ke _route_area
di app_core (area 'peraturan'). Stdlib + FastAPI; tanpa f-string; GAGAL-ANGGUN.
"""
import os
import sys
import json
import glob
import argparse
import subprocess

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
_VALID_JENIS = ("hit", "abstain")


def _norm_profile(p):
    v = (p or "").strip().lower()
    return v if v in _PROFILES else "agent"


def _strict_profile(p):
    """Kembalikan profil valid atau None (utk AKSI TULIS: jangan diam-diam
    jatuh ke 'agent' bila salah ketik)."""
    v = (p or "").strip().lower()
    return v if v in _PROFILES else None


def _as_bool(v):
    """Tafsir longgar nilai kebenaran (bool/angka/teks) -> bool."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "on", "ya", "aktif")


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


# ------------------------------------------------------ AKSI TULIS (4b) knob
def set_knob_action(profile, name, value):
    """Setel satu knob utk satu profil (store). Kembalikan knob efektif terbaru.

    Validasi profil & knob KETAT (profil salah ketik -> error, bukan diam-diam
    'agent'). INERT terhadap runtime: menulis di sini hanya mengisi knob store;
    pipeline belum membacanya (penyambungan = langkah 4e). GAGAL-ANGGUN.
    """
    if ks is None:
        return {"ok": False, "error": "knob_store tak tersedia"}
    prof = _strict_profile(profile)
    if prof is None:
        return {"ok": False, "error": "profil tak dikenal: %r (pilih: %s)"
                % (profile, ", ".join(_PROFILES))}
    try:
        res = ks.set_knob(prof, name, value)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    ov = knobs_overview(prof)
    return {"ok": True, "profile": prof, "name": res.get("name"),
            "stored": res.get("value"), "knobs": ov.get("knobs", {})}


def clear_knob_action(profile, name):
    """Hapus override knob utk satu profil -> kembali ke env/default.

    Validasi ketat + GAGAL-ANGGUN, seperti set_knob_action.
    """
    if ks is None:
        return {"ok": False, "error": "knob_store tak tersedia"}
    prof = _strict_profile(profile)
    if prof is None:
        return {"ok": False, "error": "profil tak dikenal: %r (pilih: %s)"
                % (profile, ", ".join(_PROFILES))}
    try:
        res = ks.clear_knob(prof, name)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    ov = knobs_overview(prof)
    return {"ok": True, "profile": prof, "name": res.get("name"),
            "deleted": res.get("deleted", 0), "knobs": ov.get("knobs", {})}


# ---------------------------------------------------- AKSI TULIS (4c) golden
def golden_upsert_action(query, jenis_harapan="hit", expect=None, catatan="",
                         aktif=True):
    """Tambah/ubah satu entri golden. id diturunkan dari query (gid), jadi
    meng-upsert query yang sama = mengedit entri itu. expect boleh dict atau
    string JSON. GAGAL-ANGGUN.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query wajib diisi"}
    jh = (jenis_harapan or "hit").strip().lower()
    if jh not in _VALID_JENIS:
        return {"ok": False, "error": "jenis_harapan tak dikenal: %r (pilih: %s)"
                % (jenis_harapan, ", ".join(_VALID_JENIS))}
    ex = expect
    if isinstance(ex, str):
        s = ex.strip()
        if s == "":
            ex = {}
        else:
            try:
                ex = json.loads(s)
            except Exception as e:
                return {"ok": False,
                        "error": "expect bukan JSON valid: %s" % (str(e)[:120])}
    if ex is None:
        ex = {}
    if not isinstance(ex, dict):
        return {"ok": False, "error": "expect harus objek/dict"}
    try:
        res = gdb.upsert_golden(q, jenis_harapan=jh, expect=ex,
                                catatan=(catatan or ""),
                                aktif=1 if _as_bool(aktif) else 0)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "id": res.get("id"), "query": q, "jenis_harapan": jh,
            "aktif": bool(_as_bool(aktif)),
            "golden": golden_overview(include_rows=False)}


def golden_set_aktif_action(golden_id, aktif):
    """Aktifkan/nonaktifkan satu entri golden (soft: nonaktif = keluar dari
    eval tanpa dihapus). GAGAL-ANGGUN.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    gidv = (golden_id or "").strip()
    if not gidv:
        return {"ok": False, "error": "id wajib diisi"}
    val = _as_bool(aktif)
    try:
        gdb.set_aktif(gidv, 1 if val else 0)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "id": gidv, "aktif": bool(val),
            "golden": golden_overview(include_rows=False)}


def golden_delete_action(golden_id):
    """Hapus PERMANEN satu entri golden. DESTRUKTIF — utk sekadar mengeluarkan
    dari eval, lebih aman golden_set_aktif_action(id, False). GAGAL-ANGGUN.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    gidv = (golden_id or "").strip()
    if not gidv:
        return {"ok": False, "error": "id wajib diisi"}
    conn = None
    try:
        conn = gdb.init_db(gdb.connect())
        cur = conn.execute("DELETE FROM rag_golden WHERE id=?", (gidv,))
        conn.commit()
        deleted = cur.rowcount or 0
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return {"ok": True, "id": gidv, "deleted": deleted,
            "golden": golden_overview(include_rows=False)}


def golden_seed_action(mirror=True):
    """Isi golden set bawaan (merge idempoten) + longgarkan ekspektasi v2, lalu
    (opsional) cermin ke /rag-eval. Setara CLI 'phase4_eval.py --seed'.
    GAGAL-ANGGUN per tahap.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    out = {"ok": True}
    try:
        out["seeded"] = gdb.seed_default()
    except Exception as e:
        return {"ok": False, "error": "seed_default gagal: %s" % (str(e)[:160])}
    try:
        fx = gdb.fix_seed_v2()
        out["fixed"] = fx.get("updated", 0) if isinstance(fx, dict) else 0
    except Exception as e:
        out["fixed_error"] = str(e)[:160]
    if mirror:
        try:
            out["mirror"] = gdb.mirror_to_eval()
        except Exception as e:
            out["mirror_error"] = str(e)[:160]
    out["golden"] = golden_overview(include_rows=False)
    return out


def golden_mirror_action():
    """Cerminkan golden AKTIF ke eval_sample (jenis='golden') utk LLM-judge di
    /rag-eval. GAGAL-ANGGUN.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    try:
        res = gdb.mirror_to_eval()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    ok = bool(res.get("ok")) if isinstance(res, dict) else False
    return {"ok": ok, "mirror": res,
            "golden": golden_overview(include_rows=False)}


# ------------------------------------------------------ AKSI (4d) eval & mine
def _eval_script_path():
    return os.path.join(_BASE_DIR, "phase4_eval.py")


def mine_action(limit=400, profile="agent"):
    """Kandidat golden dari feedback produksi (jempol-down / fallback), read-only.

    Sumber = db.agent_log_db (profil 'agent'). Mining feedback chatbot
    (Dialogflow) BELUM tersedia; utk profile != 'agent' kembalikan daftar
    kosong dengan catatan. Menandai kandidat yang SUDAH ada di golden set.
    GAGAL-ANGGUN.
    """
    if gdb is None:
        return {"ok": False, "error": "golden_db tak tersedia"}
    prof = _norm_profile(profile)
    out = {"ok": True, "profile": prof, "items": [], "n": 0}
    if prof != "agent":
        out["catatan"] = ("mining feedback hanya tersedia utk profil 'agent' "
                          "(log agent); chatbot belum diwire")
        return out
    try:
        lim = int(limit or 400)
    except Exception:
        lim = 400
    try:
        res = gdb.mine_feedback(limit=lim)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    if not isinstance(res, dict) or not res.get("ok"):
        return {"ok": False,
                "error": (res.get("error") if isinstance(res, dict)
                          else "mine_feedback gagal")}
    exist = set()
    try:
        for row in gdb.list_golden():
            exist.add(row.get("id"))
    except Exception:
        pass
    items = res.get("items") or []
    rows = []
    for it in items:
        q = it.get("question") or ""
        try:
            gidv = gdb.gid(q)
        except Exception:
            gidv = None
        rows.append({
            "id": gidv,
            "query": q,
            "n_down": it.get("n_down", 0),
            "n_fallback": it.get("n_fallback", 0),
            "n_total": it.get("n_total", 0),
            "last_ts": it.get("last_ts", ""),
            "sudah_di_golden": bool(gidv and gidv in exist),
        })
    out["items"] = rows
    out["n"] = len(rows)
    return out


def run_eval_action(k=10, limit=None, rewrite_ai=False, deterministik=True,
                    baseline_save=False, baseline_check=False,
                    baseline_path=None, tolerance=0.05, timeout=3600):
    """Jalankan phase4_eval.py sebagai SUB-PROSES TERISOLASI.

    Isolasi disengaja: phase4_eval menyetel env global (RAG_REWRITE_AI=0,
    CUBLAS_WORKSPACE_CONFIG) saat impor dan _setup_determinism() memaksa
    torch.use_deterministic_algorithms(True) + cudnn.deterministic utk SELURUH
    proses. Bila dijalankan in-process, itu akan MENCEMARI server produksi
    (mematikan rewrite AI live + mengubah mode torch reranker). Sub-proses
    membuat semua efek itu MATI saat proses anak selesai.

    returncode phase4_eval: 0 = sukses / gerbang OK; 1 = regresi baseline;
    2 = baseline tak terbaca / golden kosong. GAGAL-ANGGUN.
    """
    script = _eval_script_path()
    if not os.path.exists(script):
        return {"ok": False, "error": "phase4_eval.py tak ditemukan: %s" % script}
    argv = [sys.executable, "-u", script]
    try:
        argv += ["--k", str(int(k or 10))]
    except Exception:
        argv += ["--k", "10"]
    if limit is not None:
        try:
            argv += ["--limit", str(int(limit))]
        except Exception:
            pass
    bpath = _baseline_path(baseline_path)
    if baseline_save:
        argv += ["--baseline-save", bpath]
    if baseline_check:
        argv += ["--baseline-check", bpath]
    if baseline_save or baseline_check:
        try:
            argv += ["--tolerance", str(float(tolerance))]
        except Exception:
            pass
    env = dict(os.environ)
    env["PHASE4_REWRITE_AI"] = "1" if _as_bool(rewrite_ai) else "0"
    env["PHASE4_DETERMINISTIK"] = "1" if _as_bool(deterministik) else "0"
    try:
        to = int(timeout) if timeout else None
    except Exception:
        to = 3600
    try:
        proc = subprocess.run(argv, cwd=_BASE_DIR, env=env,
                              capture_output=True, text=True, timeout=to)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "eval melewati batas waktu %ss" % to,
                "argv": argv}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "argv": argv}
    rc = proc.returncode
    tail = (proc.stdout or "")[-4000:]
    err_tail = (proc.stderr or "")[-1500:]
    if rc == 0:
        ok = True
    elif rc == 1 and baseline_check:
        ok = True                 # regresi terdeteksi, bukan crash
    else:
        ok = False
    out = {
        "ok": ok,
        "returncode": rc,
        "gate_pass": (rc == 0) if baseline_check else None,
        "regresi": (rc == 1) if baseline_check else None,
        "rewrite_ai": env["PHASE4_REWRITE_AI"],
        "deterministik": env["PHASE4_DETERMINISTIK"],
        "baseline_path": bpath if (baseline_save or baseline_check) else None,
        "stdout_tail": tail,
        "latest_run": latest_runs(1),
    }
    if err_tail.strip():
        out["stderr_tail"] = err_tail
    if not ok and "error" not in out:
        if rc == 2:
            out["error"] = "eval gagal (rc=2): baseline tak terbaca / golden kosong"
        else:
            out["error"] = "eval gagal (rc=%s)" % rc
    return out


# ------------------------------------------------------------ route FastAPI
try:
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
except Exception:            # pragma: no cover
    Request = object
    JSONResponse = None
    run_in_threadpool = None


async def _read_json_body(request):
    """Baca body JSON jadi dict; kembalikan {} bila kosong/tak valid."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register(app):
    """Daftarkan endpoint harness. GET read-only (3a) + POST tulis knob (4b) +
    POST tulis golden (4c) + eval/mine (4d)."""
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

    async def api_knob_set(request: Request):
        body = await _read_json_body(request)
        prof = body.get("profile")
        name = body.get("name")
        value = body.get("value")
        try:
            res = await run_in_threadpool(set_knob_action, prof, name, value)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_knob_clear(request: Request):
        body = await _read_json_body(request)
        prof = body.get("profile")
        name = body.get("name")
        try:
            res = await run_in_threadpool(clear_knob_action, prof, name)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden_upsert(request: Request):
        body = await _read_json_body(request)
        try:
            res = await run_in_threadpool(
                golden_upsert_action,
                body.get("query"), body.get("jenis_harapan", "hit"),
                body.get("expect"), body.get("catatan", ""),
                body.get("aktif", True))
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden_aktif(request: Request):
        body = await _read_json_body(request)
        try:
            res = await run_in_threadpool(golden_set_aktif_action,
                                          body.get("id"), body.get("aktif"))
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden_delete(request: Request):
        body = await _read_json_body(request)
        try:
            res = await run_in_threadpool(golden_delete_action, body.get("id"))
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden_seed(request: Request):
        body = await _read_json_body(request)
        try:
            res = await run_in_threadpool(golden_seed_action,
                                          _as_bool(body.get("mirror", True)))
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_golden_mirror(request: Request):
        try:
            res = await run_in_threadpool(golden_mirror_action)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_mine(request: Request):
        lim = request.query_params.get("limit") or "400"
        prof = request.query_params.get("profile") or "agent"
        try:
            res = await run_in_threadpool(mine_action, lim, prof)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def api_eval_run(request: Request):
        body = await _read_json_body(request)
        try:
            res = await run_in_threadpool(
                run_eval_action,
                body.get("k", 10), body.get("limit"),
                _as_bool(body.get("rewrite_ai", False)),
                _as_bool(body.get("deterministik", True)),
                _as_bool(body.get("baseline_save", False)),
                _as_bool(body.get("baseline_check", False)),
                body.get("baseline_path"),
                body.get("tolerance", 0.05),
                body.get("timeout", 3600))
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    app.add_api_route("/api/harness/overview", api_overview, methods=["GET"])
    app.add_api_route("/api/harness/knobs", api_knobs, methods=["GET"])
    app.add_api_route("/api/harness/golden", api_golden, methods=["GET"])
    app.add_api_route("/api/harness/baseline", api_baseline, methods=["GET"])
    app.add_api_route("/api/harness/runs", api_runs, methods=["GET"])
    app.add_api_route("/api/harness/mine", api_mine, methods=["GET"])
    app.add_api_route("/api/harness/knob/set", api_knob_set, methods=["POST"])
    app.add_api_route("/api/harness/knob/clear", api_knob_clear, methods=["POST"])
    app.add_api_route("/api/harness/golden/upsert", api_golden_upsert, methods=["POST"])
    app.add_api_route("/api/harness/golden/aktif", api_golden_aktif, methods=["POST"])
    app.add_api_route("/api/harness/golden/delete", api_golden_delete, methods=["POST"])
    app.add_api_route("/api/harness/golden/seed", api_golden_seed, methods=["POST"])
    app.add_api_route("/api/harness/golden/mirror", api_golden_mirror, methods=["POST"])
    app.add_api_route("/api/harness/eval/run", api_eval_run, methods=["POST"])


# ------------------------------------------------------------ CLI uji cepat
def _cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Uji backend RAG Harness (Tahap 4 #1).")
    ap.add_argument("--section", default="overview",
                    help="overview|knobs|golden|baseline|runs")
    ap.add_argument("--profile", default="agent", help="agent|chatbot")
    ap.add_argument("--limit", default=None, type=int,
                    help="runs: jumlah laporan; mine: batas baris log (default 400)")
    ap.add_argument("--set", dest="set_name", default=None,
                    help="NAMA knob utk disetel (perlu --value)")
    ap.add_argument("--value", dest="value", default=None,
                    help="nilai utk --set")
    ap.add_argument("--clear", dest="clear_name", default=None,
                    help="NAMA knob utk dihapus (kembali ke env/default)")
    ap.add_argument("--golden-upsert", dest="golden_upsert", default=None,
                    help="QUERY utk upsert golden (perlu --jenis/--expect opsional)")
    ap.add_argument("--jenis", default="hit", help="hit|abstain (utk --golden-upsert)")
    ap.add_argument("--expect", default=None, help="JSON expect (utk --golden-upsert)")
    ap.add_argument("--catatan", default="", help="catatan (utk --golden-upsert)")
    ap.add_argument("--golden-aktif", dest="golden_aktif", default=None,
                    help="ID golden utk set aktif (pakai --aktif true|false)")
    ap.add_argument("--golden-delete", dest="golden_delete", default=None,
                    help="ID golden utk hapus PERMANEN")
    ap.add_argument("--golden-seed", dest="golden_seed", action="store_true",
                    help="seed bawaan + fix v2 + cermin ke /rag-eval")
    ap.add_argument("--golden-mirror", dest="golden_mirror", action="store_true",
                    help="cermin golden aktif ke /rag-eval")
    ap.add_argument("--aktif", dest="aktif_flag", default=None,
                    help="true|false (utk --golden-upsert / --golden-aktif)")
    ap.add_argument("--mine", dest="mine", action="store_true",
                    help="tampilkan kandidat golden dari feedback (read-only)")
    ap.add_argument("--eval-run", dest="eval_run", action="store_true",
                    help="jalankan phase4_eval (sub-proses terisolasi)")
    ap.add_argument("--k", type=int, default=10, help="recall@k utk --eval-run")
    ap.add_argument("--eval-limit", dest="eval_limit", type=int, default=None,
                    help="batasi jumlah entri golden saat --eval-run")
    ap.add_argument("--rewrite-ai", dest="rewrite_ai", action="store_true",
                    help="--eval-run mode produksi (RAG_REWRITE_AI=1)")
    ap.add_argument("--no-deterministik", dest="no_deterministik",
                    action="store_true", help="--eval-run matikan determinisme")
    ap.add_argument("--eval-baseline-save", dest="eval_baseline_save",
                    action="store_true", help="simpan baseline saat --eval-run")
    ap.add_argument("--eval-baseline-check", dest="eval_baseline_check",
                    action="store_true", help="cek regresi baseline saat --eval-run")
    ap.add_argument("--baseline-path", dest="baseline_path", default=None,
                    help="path baseline (default golden_base.json)")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="toleransi regresi utk --eval-baseline-check")
    args = ap.parse_args(argv)

    if args.set_name is not None:
        data = set_knob_action(args.profile, args.set_name, args.value)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.clear_name is not None:
        data = clear_knob_action(args.profile, args.clear_name)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1

    if args.golden_upsert is not None:
        aktif = True if args.aktif_flag is None else _as_bool(args.aktif_flag)
        data = golden_upsert_action(args.golden_upsert, args.jenis, args.expect,
                                    args.catatan, aktif)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.golden_aktif is not None:
        aktif = True if args.aktif_flag is None else _as_bool(args.aktif_flag)
        data = golden_set_aktif_action(args.golden_aktif, aktif)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.golden_delete is not None:
        data = golden_delete_action(args.golden_delete)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.golden_seed:
        data = golden_seed_action(mirror=True)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.golden_mirror:
        data = golden_mirror_action()
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1

    if args.mine:
        data = mine_action(args.limit, args.profile)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1
    if args.eval_run:
        data = run_eval_action(
            k=args.k, limit=args.eval_limit,
            rewrite_ai=args.rewrite_ai,
            deterministik=(not args.no_deterministik),
            baseline_save=args.eval_baseline_save,
            baseline_check=args.eval_baseline_check,
            baseline_path=args.baseline_path,
            tolerance=args.tolerance)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 1

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
