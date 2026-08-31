# -*- coding: utf-8 -*-
"""avaya/phone_analyze.py - Fase 3 tahap 2 (ANALISIS): STT (Qwen via worker
subprocess di .venv-asr) + LLM (phone_llm) untuk interaksi Telepon yang sudah
ditarik, lalu simpan transkrip + analisis ke awe_phone_interactions.

STT jalan sebagai SUBPROCESS ke .venv-asr agar tidak mencemari .venv utama
(versi transformers beda). LAMBAT (~1 mnt/panggilan) -> panggil dari job latar,
jangan langsung dari request web. Terpisah dari alur Chat.

Env: AWE_ASR_PY (python .venv-asr), AWE_STT_WORKER (path awe_stt_worker.py).
"""
import json
import os
import subprocess
import tempfile

import avaya.phone_llm as pllm

try:
    from .phone_db import save_phone_analysis, init_phone_db
except Exception:
    from phone_db import save_phone_analysis, init_phone_db

try:
    from .phone_glossary import asr_context as _asr_context
except Exception:
    try:
        from phone_glossary import asr_context as _asr_context
    except Exception:
        _asr_context = None


def _asr_python():
    return os.environ.get("AWE_ASR_PY") or os.path.join(".venv-asr", "Scripts", "python.exe")


def _worker_path():
    return os.environ.get("AWE_STT_WORKER") or "awe_stt_worker.py"


def pending_phone(conn, day=None, limit=25, min_durasi=0):
    """Baris dgn audio_ref tapi belum diproses tahap 2 (transkrip_source kosong)."""
    init_phone_db(conn)
    sql = ("SELECT sid, audio_ref, durasi FROM awe_phone_interactions "
           "WHERE audio_ref IS NOT NULL AND audio_ref<>'' AND transkrip_source IS NULL")
    p = []
    if day:
        sql += " AND day=?"
        p.append(str(day)[:10])
    if min_durasi:
        sql += " AND (durasi IS NULL OR durasi>=?)"
        p.append(int(min_durasi))
    sql += " ORDER BY tanggal DESC, sid DESC LIMIT ?"
    p.append(int(limit))
    return [dict(r) for r in conn.execute(sql, p).fetchall()]


def pending_llm(conn, day=None, limit=25):
    """Baris yang STT-nya sudah ada tapi analisis LLM belum jadi (mis. LLM gagal
    atau di-skip). Dipakai untuk mengulang HANYA tahap LLM tanpa STT ulang."""
    init_phone_db(conn)
    sql = ("SELECT sid, stt_text FROM awe_phone_interactions "
           "WHERE transkrip_source='qwen3-asr' "
           "AND (analisis_json IS NULL OR analisis_json='') "
           "AND stt_text IS NOT NULL AND stt_text<>''")
    p = []
    if day:
        sql += " AND day=?"
        p.append(str(day)[:10])
    sql += " ORDER BY tanggal DESC, sid DESC LIMIT ?"
    p.append(int(limit))
    return [dict(r) for r in conn.execute(sql, p).fetchall()]


def run_stt(paths, timeout=1800, context=None):
    """Jalankan awe_stt_worker.py di .venv-asr utk daftar mp4. Kembalikan JSON worker.

    context (opsional): teks kosakata/hotwords domain (Glosarium Pajak) yang
    dikirim ke worker lewat env AWE_QWEN_CONTEXT untuk membiaskan Qwen3-ASR.
    """
    paths = [p for p in (paths or []) if p]
    if not paths:
        return {"ok": False, "error": "tak ada berkas audio", "results": []}
    out_json = os.path.join(tempfile.gettempdir(), "awe_stt_out_%d.json" % os.getpid())
    cmd = [_asr_python(), _worker_path(), "--out", out_json] + list(paths)
    env = dict(os.environ)
    ctx = (context or "").strip()
    if ctx:
        env["AWE_QWEN_CONTEXT"] = ctx
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env)
    except Exception as e:
        return {"ok": False, "error": "worker gagal dijalankan: %r" % e, "results": []}
    data = None
    try:
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        tail = ""
        try:
            tail = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
        except Exception:
            pass
        data = {"ok": False, "error": "baca hasil worker gagal: %r | %s" % (e, tail), "results": []}
    try:
        os.remove(out_json)
    except Exception:
        pass
    return data


def _by_basename(results):
    m = {}
    for r in results or []:
        m[os.path.basename(str(r.get("file") or ""))] = r
    return m


def analyze_day(conn, day=None, limit=25, min_durasi=3, do_llm=True, timeout=1800):
    """Tahap 2 penuh: STT + LLM utk baris pending, PLUS ulang HANYA LLM utk baris
    yang transkripnya sudah ada tapi analisis belum jadi. Kembalikan ringkasan."""
    details = []
    stt_ok = 0
    llm_ok = 0
    llm_err = ""
    stt_error = None
    rows = pending_phone(conn, day=day, limit=limit, min_durasi=min_durasi)
    llm_rows = pending_llm(conn, day=day, limit=limit) if do_llm else []
    if not rows and not llm_rows:
        return {"ok": True, "pending": 0, "stt_ok": 0, "llm_ok": 0,
                "llm_error": "", "details": []}
    asr_ctx = ""
    if _asr_context is not None:
        try:
            asr_ctx = _asr_context() or ""
        except Exception:
            asr_ctx = ""
    if rows:
        stt = run_stt([r["audio_ref"] for r in rows], timeout=timeout, context=asr_ctx)
        stt_error = stt.get("error")
        if stt.get("error") and not stt.get("results"):
            return {"ok": False, "pending": len(rows) + len(llm_rows),
                    "stt_ok": 0, "llm_ok": 0, "error": stt.get("error"),
                    "llm_error": "", "details": []}
        res_map = _by_basename(stt.get("results"))
        for r in rows:
            sid = r["sid"]
            wr = res_map.get(os.path.basename(str(r["audio_ref"])))
            if wr is None:
                details.append({"sid": sid, "stt": False, "note": "hasil worker tak ditemukan"})
                continue
            text = (wr.get("text") or "").strip() if wr.get("ok") else ""
            info = {"model": wr.get("model"), "chunks": wr.get("chunks"),
                    "elapsed": wr.get("elapsed"), "text": text}
            if not text:
                save_phone_analysis(conn, sid, transkrip_source="kosong", stt=info)
                details.append({"sid": sid, "stt": False,
                                "note": wr.get("error") or "transkrip kosong"})
                continue
            stt_ok += 1
            analisis = None
            note = ""
            if do_llm:
                a = pllm.analyze_transcript(text)
                if a.get("ok"):
                    analisis = a.get("analysis")
                    llm_ok += 1
                else:
                    note = a.get("error") or "LLM gagal"
                    if not llm_err:
                        llm_err = note
            transkrip = (analisis or {}).get("dialog")
            save_phone_analysis(conn, sid, transkrip=transkrip,
                                transkrip_source="qwen3-asr", stt=info, analisis=analisis)
            details.append({"sid": sid, "stt": True, "llm": bool(analisis), "note": note})
    if do_llm and llm_rows:
        for r in llm_rows:
            sid = r["sid"]
            a = pllm.analyze_transcript(r.get("stt_text") or "")
            if a.get("ok"):
                analisis = a.get("analysis")
                save_phone_analysis(conn, sid,
                                    transkrip=(analisis or {}).get("dialog"),
                                    transkrip_source="qwen3-asr", analisis=analisis)
                llm_ok += 1
                details.append({"sid": sid, "stt": True, "llm": True, "note": "ulang-llm"})
            else:
                note = a.get("error") or "LLM gagal"
                if not llm_err:
                    llm_err = note
                details.append({"sid": sid, "stt": True, "llm": False, "note": note})
    return {"ok": True, "pending": len(rows) + len(llm_rows),
            "stt_ok": stt_ok, "llm_ok": llm_ok, "stt_error": stt_error,
            "llm_error": llm_err, "details": details}
