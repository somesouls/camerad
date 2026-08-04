# -*- coding: utf-8 -*-
"""awe_assess.py — Penilaian QA Agen (Assessor) untuk AWE (Avaya).

Menyediakan API baca-saja bagi assessor untuk mengambil transkrip lengkap
sebuah percakapan (user <-> agent) agar bisa dinilai manual. TIDAK ada
rubrik/skor: halaman Penilaian QA hanya FILTER percakapan berdasarkan atribut
(memakai /api/awe/analytics yang sudah ada) lalu menampilkan isi percakapan
apa adanya.

Halaman /awe/penilaian dirender oleh web_app.py (placeholder.html yang
bercabang ke templates/awe_penilaian.html saat active_page == 'awe_penilaian').
Middleware web_app.py sudah membatasi /awe/penilaian dan /api/awe/assess* ke
area "assess" (admin + assessor), jadi endpoint di sini otomatis ter-gate.

Endpoint:
  GET /api/awe/assess/transcript?sid=...[&run=...]  -> transkrip satu percakapan.

Dipasang dari awe_analytics.register(...) supaya tidak perlu menyentuh
web_app.py maupun studio_routes.py.
"""
import avaya_db as avdb
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


def register(app):
    """Pasang API transkrip Penilaian QA ke FastAPI app."""

    async def api_awe_transcript(request: Request):
        q = request.query_params
        sid = (q.get("sid") or "").strip()
        run_id = (q.get("run") or q.get("run_id") or "").strip() or None
        if not sid:
            return JSONResponse({"ok": False, "error": "Parameter sid kosong."},
                                status_code=400)

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                return avdb.get_transcript(conn, sid, run_id=run_id)
            finally:
                conn.close()

        try:
            res = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

        if not res:
            return JSONResponse({
                "ok": True, "sid": sid, "found": False, "transkrip": [],
                "note": "Transkrip belum tersimpan untuk percakapan ini. "
                        "Proses ulang data lewat Kelola Data AWE (TARIK lalu "
                        "PROSES) agar transkrip terisi.",
            })
        res["ok"] = True
        res["found"] = True
        return JSONResponse(res)

    app.add_api_route("/api/awe/assess/transcript", api_awe_transcript,
                      methods=["GET"])
