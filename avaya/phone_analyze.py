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


def run_stt(paths, timeout=1800):
    """Jalankan awe_stt_worker.py di .venv-asr utk daftar mp4. Kembalikan JSON worker."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return {"ok": False, "error": "tak ada berkas audio", "results": []}
    out_json = os.path.join(tempfile.gettempdir(), "awe_stt_out_%d.json" % os.getpid())
    cmd = [_asr_python(), _worker_path(), "--out", out_json] + list(paths)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
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
    """Tahap 2 penuh: STT + LLM utk baris pending, lalu simpan. Kembalikan ringkasan."""
    rows = pending_phone(conn, day=day, limit=limit, min_durasi=min_durasi)
    if not rows:
        return {"ok": True, "pending": 0, "stt_ok": 0, "llm_ok": 0, "details": []}
    stt = run_stt([r["audio_ref"] for r in rows], timeout=timeout)
    if stt.get("error") and not stt.get("results"):
        return {"ok": False, "pending": len(rows), "stt_ok": 0, "llm_ok": 0,
                "error": stt.get("error"), "details": []}
    res_map = _by_basename(stt.get("results"))
    details = []
    stt_ok = 0
    llm_ok = 0
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
        transkrip = (analisis or {}).get("dialog")
        save_phone_analysis(conn, sid, transkrip=transkrip,
                            transkrip_source="qwen3-asr", stt=info, analisis=analisis)
        details.append({"sid": sid, "stt": True, "llm": bool(analisis), "note": note})
    return {"ok": True, "pending": len(rows), "stt_ok": stt_ok, "llm_ok": llm_ok,
            "stt_error": stt.get("error"), "details": details}
