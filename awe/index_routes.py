# -*- coding: utf-8 -*-
"""awe/index_routes.py — Endpoint status/reindex index vektor AWE + auto-reindex.

Dipasang saat import (dari rag/rerank_patch.py, setelah rag.awe_speedup_patch)
lewat objek `app` global di app_core. Semua fail-open: bila app/index belum
siap, route & thread dilewati tanpa mengganggu boot.

Rute (area akses "awe"):
  GET  /api/awe/index/stats   -> status index vektor (vec, db)
  POST /api/awe/index/reindex -> rebuild inkremental index vektor AWE

Auto-reindex: thread latar memantau perubahan tabel awe_conversations
(COUNT, MAX(rowid)) tiap AWE_REINDEX_EVERY_S detik (default 300; 0 = mati),
lalu membangun ulang inkremental HANYA bila ada data baru/berubah. Tidak
membangun saat boot (itu tugas rag.awe_speedup_patch), hanya menyusul saat
ada data AWE baru (mis. setelah auto-pull harian).
"""
import os
import sys
import time
import threading


def _index_on():
    return str(os.environ.get("AWE_INDEX", "1")).strip().lower() not in (
        "0", "", "false", "no", "off")


def _reindex_every_s():
    try:
        return int(os.environ.get("AWE_REINDEX_EVERY_S", "300") or "300")
    except Exception:
        return 300


def _asi():
    try:
        import avaya.semantic_index as asi
        return asi
    except Exception:
        return None


def _sig_now():
    """(COUNT, MAX(rowid)) tabel awe_conversations; None bila gagal."""
    try:
        import avaya.db as _avdb
        c = _avdb.init_db(_avdb.connect())
        try:
            row = c.execute(
                "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM awe_conversations"
            ).fetchone()
            return (int(row[0]), int(row[1]))
        finally:
            c.close()
    except Exception:
        return None


def _build(force=False):
    asi = _asi()
    if asi is None:
        return {"ok": False, "n": 0, "reason": "avaya.semantic_index tak tersedia"}
    try:
        return asi.build(force)
    except Exception as e:
        return {"ok": False, "n": 0, "reason": str(e)[:160]}


def _stats():
    asi = _asi()
    if asi is None:
        return {"vec": 0}
    try:
        return asi.stats()
    except Exception as e:
        return {"vec": 0, "reason": str(e)[:160]}


# --- Auto-reindex latar (poll perubahan, inkremental, fail-open) ---
_AUTO_STARTED = False


def _auto_reindex_loop():
    every = _reindex_every_s()
    last = _sig_now()
    while True:
        try:
            time.sleep(every)
            if not _index_on():
                continue
            sig = _sig_now()
            if sig is not None and sig != last:
                res = _build(False)
                last = _sig_now()
                print("[rag_awe_index] auto-reindex:", res, flush=True)
        except Exception as e:
            print("[rag_awe_index] auto-reindex dilewati:", e, flush=True)


def _start_auto():
    global _AUTO_STARTED
    if _AUTO_STARTED or _reindex_every_s() <= 0 or not _index_on():
        return
    _AUTO_STARTED = True
    try:
        threading.Thread(target=_auto_reindex_loop, name="awe-reindex",
                         daemon=True).start()
    except Exception as e:
        print("[rag_awe_index] thread auto-reindex gagal:", e, flush=True)


# --- Registrasi rute lewat app global (app_core), fail-open ---
def _register_routes():
    appmod = sys.modules.get("app_core")
    if appmod is None:
        return
    app = getattr(appmod, "app", None)
    if app is None:
        return
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    async def awe_index_stats(request):
        def _do():
            return {"ok": True, "index": _stats()}
        return JSONResponse(await run_in_threadpool(_do))

    async def awe_index_reindex(request):
        def _do():
            return _build(False)
        return JSONResponse(await run_in_threadpool(_do))

    app.add_api_route("/api/awe/index/stats", awe_index_stats, methods=["GET"])
    app.add_api_route("/api/awe/index/reindex", awe_index_reindex, methods=["POST"])
    print("[rag_awe_index] rute /api/awe/index/* terpasang", flush=True)


def _install():
    try:
        _register_routes()
    except Exception as e:
        print("[rag_awe_index] registrasi rute dilewati:", e, flush=True)
    try:
        _start_auto()
    except Exception as e:
        print("[rag_awe_index] auto-reindex tak dimulai:", e, flush=True)


_install()
