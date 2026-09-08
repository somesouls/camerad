# -*- coding: utf-8 -*-
"""awe_assess.py — Penilaian QA Agen (Assessor) untuk AWE (Avaya).

Endpoint:
  GET /api/awe/assess/transcript?sid=...[&run=...]  -> transkrip + skor softskill.
  GET /api/awe/assess/list?range=&agent=&poro=&jenis=&ss_lengkap=...  -> daftar percakapan.
"""
import avaya.db as avdb
from awe.botfilter import wants_exclude, is_bot_name
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
    #   exclude_bot: 1|0 (opsional, default 1 -> sembunyikan percakapan bot only)
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
        exclude_bot = wants_exclude(q)
        # Bila mengecualikan bot only, ambil lebih banyak baris lalu saring &
        # potong kembali ke `limit` agar daftar tidak menyusut drastis.
        fetch_limit = min(limit * 3, 1000) if exclude_bot else limit

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
                    ss_lengkap=ss_lengkap, ss_attrs=ss_attrs, limit=fetch_limit,
                )
            finally:
                conn.close()

        try:
            res = await run_in_threadpool(_run)
        except Exception as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=500)

        # Kecualikan percakapan "bot only" (mis. "Chatbot, Google") dari daftar
        # maupun dropdown agent bila checkbox aktif (default).
        if exclude_bot and isinstance(res, dict):
            convs = res.get("conversations")
            if isinstance(convs, list):
                convs = [c for c in convs
                         if not is_bot_name((c or {}).get("agent_name"))]
                res["conversations"] = convs[:limit]
            ags = res.get("agents")
            if isinstance(ags, list):
                res["agents"] = [a for a in ags if not is_bot_name(a)]

        res["ok"] = True
        return JSONResponse(res)

    app.add_api_route("/api/awe/assess/list", api_awe_assess_list,
                      methods=["GET"])
