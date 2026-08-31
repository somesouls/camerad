# -*- coding: utf-8 -*-
"""awe/phone_jobs.py - job latar + baca data untuk menu Kelola Data Phone.

Terpisah dari phone_routes.py agar tiap berkas kecil & aman push. Meniru pola
job Chat di awe/routes.py (_AWE_PULL_JOBS): mulai thread -> progress -> fetch,
kredensial login-then-forget (tidak disimpan). Tahap 1 (tarik) butuh login
(kredensial dari .env, sama seperti AWE Chat); Tahap 2 (analisis STT+LLM) tidak
(audio sudah lokal). Chat tak tersentuh.
"""
import os
import sqlite3
import threading as _threading
import uuid as _uuid

import avaya.db as avdb
import avaya.client as avc
import avaya.phone as avphone
import avaya.phone_pull as ppull
import avaya.phone_analyze as panalyze
import avaya.phone_query as pquery
import avaya.phone_daily as pdaily

_JOBS = {}
_LOCK = _threading.Lock()


def _job_set(job_id, **kw):
    with _LOCK:
        j = _JOBS.setdefault(job_id, {})
        j.update(kw)


def job_get(job_id):
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else {}


def job_fetch(job_id):
    """Ambil status; kalau sudah selesai, buang dari memori."""
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return None
        if not j.get("finished"):
            return {"pending": True, "progress": dict(j)}
        _JOBS.pop(job_id, None)
        return dict(j)


def _conn():
    conn = avdb.connect()
    try:
        conn.row_factory = sqlite3.Row
    except Exception:
        pass
    return conn


# ---------- Tahap 1: TARIK (async, butuh login) ----------
def _pull_worker(job_id, day_from, day_to, username, password, base_url, limit, pulled_by):
    try:
        _job_set(job_id, status="login", message="Login ke Avaya WFO")
        client = avphone.AvayaPhoneClient(base_url=(base_url or None))
        client.login(username, password)
        username = None
        password = None
        _job_set(job_id, status="pull", message="Menarik & mengunduh audio telepon")
        conn = _conn()
        try:
            res = ppull.pull_day(client, conn, day_from, day_to, limit=limit, pulled_by=pulled_by)
        finally:
            conn.close()
        _job_set(job_id, status="done", finished=True, ok=True,
                 staged=res.get("staged"), with_audio=res.get("with_audio"),
                 n_audio_rows=res.get("n_audio_rows"), n_rows_total=res.get("n_rows_total"),
                 details=res.get("details"),
                 message="Tarik selesai: %s interaksi tersimpan, %s dengan audio." % (
                     res.get("staged"), res.get("with_audio")))
    except avc.AvayaAuthError as e:
        _job_set(job_id, status="error", finished=True, ok=False, need_login=True, error=str(e))
    except Exception as e:
        _job_set(job_id, status="error", finished=True, ok=False, need_login=False, error=str(e))


def start_pull(day_from, day_to, limit=25, pulled_by=""):
    # Kredensial diambil dari .env (AVAYA_USERNAME/AVAYA_PASSWORD/AVAYA_BASE_URL),
    # sama seperti alur AWE Chat (auto-pull). Login-then-forget: dipakai di worker
    # lalu dilupakan, tidak pernah ditulis ke disk/DB.
    username = (os.environ.get("AVAYA_USERNAME") or "").strip()
    password = os.environ.get("AVAYA_PASSWORD") or ""
    base_url = (os.environ.get("AVAYA_BASE_URL") or "").strip()
    job_id = _uuid.uuid4().hex
    _job_set(job_id, status="queued", finished=False, ok=None, message="Menyiapkan")
    _threading.Thread(target=_pull_worker,
                      args=(job_id, day_from, day_to, username, password, base_url, limit, pulled_by),
                      daemon=True).start()
    return job_id


# ---------- Tahap 2: ANALISIS (async, tanpa login) ----------
def _analyze_worker(job_id, day, limit, min_durasi):
    try:
        _job_set(job_id, status="stt", message="STT (Qwen) + analisis LLM berjalan")
        conn = _conn()
        try:
            res = panalyze.analyze_day(conn, day=day or None, limit=limit, min_durasi=min_durasi)
        finally:
            conn.close()
        ok = bool(res.get("ok"))
        if ok:
            msg = "Analisis selesai: %s STT ok, %s LLM ok dari %s antre." % (
                res.get("stt_ok"), res.get("llm_ok"), res.get("pending"))
            le = res.get("llm_error")
            if le:
                msg += " Catatan LLM: " + str(le)[:300]
        else:
            msg = res.get("error") or "Analisis gagal."
        _job_set(job_id, status=("done" if ok else "error"), finished=True, ok=ok,
                 pending_n=res.get("pending"), stt_ok=res.get("stt_ok"),
                 llm_ok=res.get("llm_ok"), error=res.get("error"),
                 llm_error=res.get("llm_error"), details=res.get("details"),
                 message=msg)
    except Exception as e:
        _job_set(job_id, status="error", finished=True, ok=False, error=str(e))


def start_analyze(day="", limit=25, min_durasi=3):
    job_id = _uuid.uuid4().hex
    _job_set(job_id, status="queued", finished=False, ok=None, message="Menyiapkan")
    _threading.Thread(target=_analyze_worker,
                      args=(job_id, day, limit, min_durasi), daemon=True).start()
    return job_id


# ---------- Versi SINKRON (untuk auto-pull penjadwal) ----------
def pull_sync(day_from, day_to, limit=25, pulled_by=""):
    """Tarik (Tahap 1) SINKRON - blok sampai selesai; utk auto-pull penjadwal.

    Sama seperti start_pull tetapi tanpa thread; kembalikan dict hasil job.
    Kredensial dari .env (login-then-forget).
    """
    username = (os.environ.get("AVAYA_USERNAME") or "").strip()
    password = os.environ.get("AVAYA_PASSWORD") or ""
    base_url = (os.environ.get("AVAYA_BASE_URL") or "").strip()
    job_id = _uuid.uuid4().hex
    _pull_worker(job_id, day_from, day_to, username, password, base_url, limit, pulled_by)
    res = job_get(job_id)
    with _LOCK:
        _JOBS.pop(job_id, None)
    return res


def analyze_sync(day="", limit=25, min_durasi=3):
    """Analisis (Tahap 2 STT+LLM) SINKRON; utk auto-pull opsional (LAMBAT)."""
    job_id = _uuid.uuid4().hex
    _analyze_worker(job_id, day, limit, min_durasi)
    res = job_get(job_id)
    with _LOCK:
        _JOBS.pop(job_id, None)
    return res


# ---------- Baca (sinkron) untuk UI menu ----------
def coverage(day_from=None, day_to=None):
    conn = _conn()
    try:
        return {"coverage": pquery.phone_coverage(conn, day_from or None, day_to or None),
                "stats": pquery.phone_stats(conn)}
    finally:
        conn.close()


def list_rows(day_from=None, day_to=None, limit=200):
    conn = _conn()
    try:
        return pquery.list_phone(conn, day_from or None, day_to or None, limit=limit)
    finally:
        conn.close()


def detail(sid):
    conn = _conn()
    try:
        return pquery.get_phone_interaction(conn, sid)
    finally:
        conn.close()


def daily_users(day_from=None, day_to=None, limit=1000):
    """Agregasi Pengguna Harian telepon (per ANI) untuk rentang tanggal."""
    conn = _conn()
    try:
        preset = "custom" if (day_from or day_to) else "30d"
        s, e = pdaily.resolve_range(preset, day_from or None, day_to or None)
        data = pdaily.compute(conn, s, e, limit_users=int(limit or 1000))
        data["bounds"] = pdaily.data_bounds(conn)
        return data
    finally:
        conn.close()


def daily_conversations(ani, day_from=None, day_to=None, limit=500):
    """Daftar panggilan satu nomor telepon (untuk modal detail pengguna)."""
    conn = _conn()
    try:
        preset = "custom" if (day_from or day_to) else "all"
        s, e = pdaily.resolve_range(preset, day_from or None, day_to or None)
        convs, truncated = pdaily.caller_conversations(
            conn, s, e, ani=ani, limit=int(limit or 500))
        return {"conversations": convs, "truncated": truncated}
    finally:
        conn.close()


# =============================================================
# Auto-pull harian Kelola Data Telepon (mirror auto-pull Livechat)
#   phone_autopull mendaftarkan rute /api/awe/phone/autopull/* & (opsional)
#   memulai penjadwal harian. Fail-soft: kegagalan di sini TIDAK boleh
#   mematikan menu Telepon. Diletakkan di BAWAH agar pull_sync/analyze_sync
#   sudah terdefinisi saat phone_autopull diimpor.
# =============================================================
try:
    import awe.phone_autopull as _pautopull
    _pautopull.register_app()
    _pautopull.maybe_start_scheduler()
except Exception as _pa_exc:
    print("[AWE-PHONE] auto-pull dilewati:", _pa_exc, flush=True)
