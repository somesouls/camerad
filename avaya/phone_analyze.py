# -*- coding: utf-8 -*-
"""avaya/phone_analyze.py - Fase 3 tahap 2 (ANALISIS): STT (Qwen via worker
subprocess di .venv-asr) + LLM (phone_llm) untuk interaksi Telepon yang sudah
ditarik, lalu simpan transkrip + analisis ke awe_phone_interactions.

STT jalan sebagai SUBPROCESS ke .venv-asr agar tidak mencemari .venv utama
(versi transformers beda). LAMBAT (~1 mnt/panggilan) -> panggil dari job latar,
jangan langsung dari request web. Terpisah dari alur Chat.

Env: AWE_ASR_PY (python .venv-asr), AWE_STT_WORKER (path awe_stt_worker.py).
Dwi-kanal (opsional): AWE_STT_DUAL_CHANNEL aktifkan di worker; AWE_STT_AGENT_CHANNEL
(0/1, default 0) menentukan kanal mana yang dianggap AGEN.
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


def _agent_channel():
    """Indeks kanal yang dianggap AGEN (0/1). Sisanya = penelepon."""
    try:
        return int(os.environ.get("AWE_STT_AGENT_CHANNEL") or 0)
    except Exception:
        return 0


def _dual_segments(channels):
    """Gabung segmen 2 kanal -> daftar berwaktu berlabel penutur (urut waktu).

    channels: [{ch:int, text, segments:[{start,end,text}]}]. Kanal == kanal agen
    (AWE_STT_AGENT_CHANNEL, default 0) diberi label 'Agen', sisanya 'Penelepon'.
    """
    ac = _agent_channel()
    merged = []
    for c in channels or []:
        try:
            ch = int(c.get("ch"))
        except Exception:
            ch = 0
        role = "Agen" if ch == ac else "Penelepon"
        for s in c.get("segments") or []:
            teks = (s.get("text") or "").strip()
            if not teks:
                continue
            try:
                start = float(s.get("start") or 0.0)
                end = float(s.get("end") or 0.0)
            except Exception:
                start = end = 0.0
            merged.append({"start": start, "end": end, "text": teks, "penutur": role})
    merged.sort(key=lambda x: x["start"])
    return merged or None


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


def analyze_day(conn, day=None, limit=25, min_durasi=3, do_llm=True, do_stt=True, timeout=1800):
    """Tahap 2: STT + LLM utk baris pending. do_stt/do_llm memilih fase -
    keduanya=transkrip+analisis; do_stt saja='Transkrip saja'; do_llm saja=
    'Analisis LLM saja' (ulang HANYA LLM utk baris yg transkripnya sudah ada
    tapi analisis belum jadi/gagal, tanpa STT ulang). Kembalikan ringkasan."""
    details = []
    stt_ok = 0
    llm_ok = 0
    llm_err = ""
    stt_error = None
    rows = pending_phone(conn, day=day, limit=limit, min_durasi=min_durasi) if do_stt else []
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
            is_dual = bool(wr.get("ok") and wr.get("dual") and wr.get("channels"))
            seg_arg = _dual_segments(wr.get("channels")) if is_dual else None
            info = {"model": wr.get("model"), "chunks": wr.get("chunks"),
                    "elapsed": wr.get("elapsed"), "text": text, "dual": is_dual}
            if not text:
                save_phone_analysis(conn, sid, transkrip_source="kosong", stt=info)
                details.append({"sid": sid, "stt": False,
                                "note": wr.get("error") or "transkrip kosong"})
                continue
            stt_ok += 1
            analisis = None
            note = ""
            if do_llm:
                a = pllm.analyze_transcript(text, segments=seg_arg)
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
            details.append({"sid": sid, "stt": True, "llm": bool(analisis),
                            "note": note, "dual": is_dual})
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


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except Exception:
        return int(default)


def _count_pending(conn, day=None, min_durasi=3):
    """(n_stt, n_llm): jumlah baris butuh STT & jumlah baris butuh LLM saja."""
    init_phone_db(conn)
    p = []
    sql = ("SELECT COUNT(*) FROM awe_phone_interactions WHERE audio_ref IS NOT NULL "
           "AND audio_ref<>'' AND transkrip_source IS NULL")
    if day:
        sql += " AND day=?"
        p.append(str(day)[:10])
    if min_durasi:
        sql += " AND (durasi IS NULL OR durasi>=?)"
        p.append(int(min_durasi))
    n_stt = int(conn.execute(sql, p).fetchone()[0] or 0)
    p2 = []
    sql2 = ("SELECT COUNT(*) FROM awe_phone_interactions WHERE transkrip_source='qwen3-asr' "
            "AND (analisis_json IS NULL OR analisis_json='') AND stt_text IS NOT NULL AND stt_text<>''")
    if day:
        sql2 += " AND day=?"
        p2.append(str(day)[:10])
    n_llm = int(conn.execute(sql2, p2).fetchone()[0] or 0)
    return n_stt, n_llm


def analyze_all(conn, day=None, min_durasi=3, batch=None, max_batches=None,
                do_llm=True, do_stt=True, timeout=None, on_prog=None, should_stop=None):
    """Ulang analyze_day per-batch SAMPAI HABIS (resumable). Fase dipilih lewat
    do_stt/do_llm: keduanya = transkrip + analisis semua; do_stt saja =
    'Transkrip semua'; do_llm saja = 'Analisis LLM semua' (ulang LLM utk baris
    yang sudah ditranskrip). Berhenti bila antrean fase terkait habis, atau satu
    putaran tanpa sisa STT tak menghasilkan LLM sukses (hindari loop pd baris yg
    gagal terus), atau rounds >= max_batches (pengaman keras).
    """
    batch = int(batch or _int_env("AWE_PHONE_STT_BATCH", 8))
    if batch < 1:
        batch = 8
    if max_batches is None:
        max_batches = _int_env("AWE_PHONE_ANALYZE_MAXBATCH", 500)
    if timeout is None:
        timeout = _int_env("AWE_PHONE_STT_BATCH_TIMEOUT", 2400)
    rounds = 0
    stt_ok = 0
    llm_ok = 0
    llm_err = ""
    last_err = None
    while True:
        if should_stop and should_stop():
            break
        n_stt, n_llm = _count_pending(conn, day=day, min_durasi=min_durasi)
        relevant = (n_stt if do_stt else 0) + (n_llm if do_llm else 0)
        if relevant == 0:
            break
        if rounds >= max_batches:
            break
        if on_prog:
            on_prog("Batch %d - sisa %d STT, %d LLM..." % (rounds + 1, n_stt, n_llm))
        res = analyze_day(conn, day=day, limit=batch, min_durasi=min_durasi,
                          do_llm=do_llm, do_stt=do_stt, timeout=timeout)
        rounds += 1
        if not res.get("ok"):
            last_err = res.get("error") or "STT/analisis gagal"
            break
        stt_ok += int(res.get("stt_ok") or 0)
        llm_ok += int(res.get("llm_ok") or 0)
        if res.get("llm_error") and not llm_err:
            llm_err = res.get("llm_error")
        stt_left = n_stt if do_stt else 0
        if stt_left == 0 and int(res.get("llm_ok") or 0) == 0:
            break
    n_stt, n_llm = _count_pending(conn, day=day, min_durasi=min_durasi)
    remaining = (n_stt if do_stt else 0) + (n_llm if do_llm else 0)
    return {"ok": last_err is None, "all": True, "rounds": rounds,
            "stt_ok": stt_ok, "llm_ok": llm_ok, "pending": remaining,
            "remaining_stt": n_stt, "remaining_llm": n_llm,
            "error": last_err, "llm_error": llm_err, "details": []}
