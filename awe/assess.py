# -*- coding: utf-8 -*-
"""awe_assess.py — Penilaian QA Agen (Assessor) untuk AWE (Avaya).

Endpoint:
  GET /api/awe/assess/transcript?sid=...[&run=...]  -> transkrip + skor softskill.
  GET /api/awe/assess/list?range=&agent=&poro=&jenis=&ss_lengkap=...  -> daftar percakapan.
"""
import avaya.db as avdb
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool


def register(app):
    """Pasang API Penilaian QA ke FastAPI app."""

    # ------------------------------------------------------------------
    # GET /api/awe/assess/transcript  — transkrip + skor softskill
    # ------------------------------------------------------------------
    async def api_awe_transcript(request: Request):
        q = request.query_params
        sid    = (q.get("sid")    or "").strip()
        run_id = (q.get("run")    or q.get("run_id") or "").strip() or None
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
                "note": "Transkrip belum tersimpan. Proses ulang data lewat "
                        "Kelola Data AWE (TARIK lalu PROSES) agar transkrip terisi.",
            })
        res["ok"] = True
        res["found"] = True
        return JSONResponse(res)

    app.add_api_route("/api/awe/assess/transcript", api_awe_transcript,
                      methods=["GET"])

    # ------------------------------------------------------------------
    # GET /api/awe/assess/list  — daftar percakapan untuk Penilaian QA
    # Query params:
    #   range      : today|yesterday|7d|30d|90d|all|custom  (default: 7d)
    #   start,end  : YYYY-MM-DD (hanya untuk range=custom)
    #   agent      : nama agent (substring, opsional)
    #   poro       : ya|tidak|"" (opsional)
    #   jenis      : nama jenis layanan tepat (opsional)
    #   ss_lengkap : ya|tidak|"" (opsional)
    #   ss_<attr>  : ya|tidak (opsional, per atribut softskill)
    #   limit      : integer, default 200
    # ------------------------------------------------------------------
    async def api_awe_assess_list(request: Request):
        q          = request.query_params
        range_     = (q.get("range")     or "7d").strip()
        start      = (q.get("start")     or "").strip()
        end        = (q.get("end")       or "").strip()
        agent      = (q.get("agent")     or "").strip()
        poro       = (q.get("poro")      or "").strip()
        jenis      = (q.get("jenis")     or "").strip()
        ss_lengkap = (q.get("ss_lengkap") or "").strip()
        limit      = min(int(q.get("limit") or 200), 1000)

        _SS_ATTRS = ["salam_pembuka", "menanyakan_nama", "menyapa_customer",
                     "menawarkan_bantuan", "hold", "salam_penutup"]
        ss_attrs = {}
        for attr in _SS_ATTRS:
            v = (q.get("ss_" + attr) or "").strip()
            if v in ("ya", "tidak"):
                ss_attrs[attr] = v

        def _run():
            conn = avdb.init_db(avdb.connect())
            try:
                return avdb.list_for_assess(
                    conn, range_=range_, start=start, end=end,
                    agent=agent, poro=poro, jenis=jenis,
                    ss_lengkap=ss_lengkap, ss_attrs=ss_attrs, limit=limit,
                )
            finally:
                conn.close()

        try:
            res = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

        res["ok"] = True
        return JSONResponse(res)

    app.add_api_route("/api/awe/assess/list", api_awe_assess_list,
                      methods=["GET"])
