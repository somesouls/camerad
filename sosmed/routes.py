# -*- coding: utf-8 -*-
"""sosmed_routes.py — Rute Menu Sosmed (X / IG / TikTok), versi ringkas 4 menu.

Struktur menu (rombak Agustus 2026, sesuai arahan "cukup jadi database FAQ"):
  1. Q&A                   — gabungan Inbox + Daftar Q&A (pertanyaan warga + utas).
  2. Kelola Data Sosmed    — impor manual / tarik X + housekeeping + perbaiki data.
  3. SLA & Analitik        — gabungan Coverage & SLA + Analitik Sosmed.
  4. Coverage & Deflection — database FAQ: klaster pertanyaan yang lagi trending +
                             cek apakah bot SUDAH punya intent yang menjawab
                             (bila belum -> gap pengetahuan), lengkap draf jawaban.

Semua menu punya filter platform (X / IG / TikTok), siap untuk impor IG & TikTok.

Lapisan data: sosmed_db.py (pairing Q&A sadar-thread). Otak FAQ/gap:
sosmed_knowledge.py. Collector X: sosmed_x.py. Daftarkan dengan:
    import sosmed_routes; sosmed_routes.register(app)
"""
import io
import os
import csv
import json
import zipfile
import threading
import importlib

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.concurrency import run_in_threadpool

import sosmed.db as sdb
import sosmed.x as sx
import sosmed.knowledge as sk
import sosmed.semantic_index as ssi
import sosmed.monitor as smon
from app_core import render_page


def _conn():
    c = sdb.connect()
    sdb.init_db(c)
    smon.ensure_review_columns(c)
    return c


def _off_handles():
    return sdb.official_handles()


# ---------------------------------------------------------------------------
# Auto-reindex index vektor sosmed (latar, fail-open, coalesced)
# ---------------------------------------------------------------------------
_REINDEX_LOCK = threading.Lock()
_REINDEX_STATE = {"running": False, "again": False}


def _auto_reindex_on():
    return str(os.environ.get("SOSMED_AUTO_REINDEX", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _kick_reindex_bg():
    """Jadwalkan rebuild index vektor sosmed di latar.

    Inkremental (hanya baris baru/berubah), fail-open, dan digabung (coalesced)
    supaya rentetan impor/kurasi tidak menelurkan banyak build sekaligus. Jika
    satu build sedang jalan lalu ada pemicu baru, satu build susulan dijalankan
    setelahnya agar data terakhir pasti ikut terindeks.
    """
    if not _auto_reindex_on():
        return
    with _REINDEX_LOCK:
        if _REINDEX_STATE["running"]:
            _REINDEX_STATE["again"] = True
            return
        _REINDEX_STATE["running"] = True

    def _run():
        try:
            while True:
                try:
                    ssi.build()
                except Exception:
                    pass
                with _REINDEX_LOCK:
                    if _REINDEX_STATE["again"]:
                        _REINDEX_STATE["again"] = False
                        continue
                    _REINDEX_STATE["running"] = False
                    return
        except Exception:
            with _REINDEX_LOCK:
                _REINDEX_STATE["running"] = False
                _REINDEX_STATE["again"] = False

    try:
        threading.Thread(target=_run, name="sosmed-reindex", daemon=True).start()
    except Exception:
        with _REINDEX_LOCK:
            _REINDEX_STATE["running"] = False
            _REINDEX_STATE["again"] = False


# ---------------------------------------------------------------------------
# Parsing item dari file/teks (JSON array, JSONL, CSV, payload X API v2, .zip)
# ---------------------------------------------------------------------------
def _looks_like_x_payload(obj):
    return isinstance(obj, dict) and "data" in obj and (
        "includes" in obj or "meta" in obj
        or (isinstance(obj.get("data"), list)
            and obj["data"] and isinstance(obj["data"][0], dict)
            and ("text" in obj["data"][0] or "author_id" in obj["data"][0])))


def _parse_items_text(text, name="", default_platform=None):
    """Parse satu blob teks jadi list item mentah (dict)."""
    name = (name or "").lower()
    text = (text or "").strip()
    if not text:
        return []
    # CSV
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))
        return [dict(r) for r in rows]
    # JSON / JSONL
    try:
        obj = json.loads(text)
    except Exception:
        # coba JSONL
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
        return items
    # payload X API v2 -> map lewat collector
    if _looks_like_x_payload(obj):
        return sx.map_tweets_v2(obj, official_handles=_off_handles())
    if isinstance(obj, dict) and isinstance(obj.get("items"), list):
        return obj["items"]
    if isinstance(obj, dict) and isinstance(obj.get("data"), list):
        return obj["data"]
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    return []


def _parse_items_upload(data, name, default_platform=None):
    name = (name or "").lower()
    if name.endswith(".zip"):
        items = []
        zf = zipfile.ZipFile(io.BytesIO(data))
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                raw = zf.read(info).decode("utf-8", "replace")
            except Exception:
                continue
            items.extend(_parse_items_text(raw, info.filename, default_platform))
        return items
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    return _parse_items_text(text, name, default_platform)


# ---------------------------------------------------------------------------
# Halaman menu (4 menu)
# ---------------------------------------------------------------------------
async def sosmed_qna_page(request: Request):
    return render_page(request, "sosmed_qna.html", "sosmed_qna")


async def sosmed_kelola_page(request: Request):
    return render_page(request, "sosmed_kelola.html", "sosmed_kelola")


async def sosmed_sla_page(request: Request):
    return render_page(request, "sosmed_sla.html", "sosmed_sla")


async def sosmed_deflection_page(request: Request):
    return render_page(request, "sosmed_deflection.html", "sosmed_deflection")


async def sosmed_monitor_page(request: Request):
    return render_page(request, "sosmed_monitor.html", "sosmed_monitor")


# Redirect rute lama -> baru (kompatibilitas bookmark setelah rombak menu)
async def _redir_qna(request: Request):
    return RedirectResponse("/sosmed", status_code=307)


async def _redir_sla(request: Request):
    return RedirectResponse("/sosmed/sla", status_code=307)


async def _redir_deflection(request: Request):
    return RedirectResponse("/sosmed/deflection", status_code=307)


# ---------------------------------------------------------------------------
# Impor manual
# ---------------------------------------------------------------------------
def _current_username(request: Request):
    try:
        u = getattr(request.state, "user", None)
        return (u or {}).get("username") or ""
    except Exception:
        return ""


async def api_import_upload(request: Request):
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"ok": False, "error": "Form tidak valid."}, status_code=400)
    platform = (form.get("platform") or "").strip().lower() or None
    ups = [u for u in form.getlist("file") if isinstance(u, StarletteUploadFile)]
    if not ups:
        return JSONResponse({"ok": False, "error": "Tidak ada berkas diunggah."}, status_code=400)
    items = []
    for u in ups:
        data = await u.read()
        items.extend(_parse_items_upload(data, u.filename or "data.json", platform))
    if not items:
        return JSONResponse({"ok": False, "error": "Tidak ada item terbaca dari berkas."}, status_code=400)
    user = _current_username(request)

    def _do():
        c = _conn()
        try:
            return sdb.ingest_items(c, items, default_platform=platform,
                                    source="import_upload", pulled_by=user)
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok", True):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


async def api_import_paste(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = (body or {}).get("content") or ""
    platform = ((body or {}).get("platform") or "").strip().lower() or None
    fmt = ((body or {}).get("format") or "").strip().lower()
    name = ".csv" if fmt == "csv" else ".json"
    items = _parse_items_text(content, name, platform)
    if not items:
        return JSONResponse({"ok": False, "error": "Tidak ada item terbaca dari teks."}, status_code=400)
    user = _current_username(request)

    def _do():
        c = _conn()
        try:
            return sdb.ingest_items(c, items, default_platform=platform,
                                    source="import_paste", pulled_by=user)
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok", True):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


# ---------------------------------------------------------------------------
# Tarik X (capability-aware)
# ---------------------------------------------------------------------------
async def api_x_capabilities(request: Request):
    cap = await run_in_threadpool(sx.capabilities)
    return JSONResponse({"ok": True, "capabilities": cap})


async def api_pull_x(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    source = ((body or {}).get("source") or "mentions").strip().lower()
    query = ((body or {}).get("query") or "").strip()
    try:
        max_results = int((body or {}).get("max_results") or 50)
    except Exception:
        max_results = 50
    user = _current_username(request)
    off = _off_handles()

    def _do():
        if source == "search":
            q = query or "kringpajak"
            items, info = sx.search_recent(q, max_results=max_results, official_handles=off)
        else:
            items, info = sx.pull_mentions(max_results=max_results, official_handles=off)
        if not info.get("ok"):
            return {"ok": False, "error": info.get("error", "Gagal menarik data X."),
                    "capability": True}
        c = _conn()
        try:
            res = sdb.ingest_items(c, items, default_platform="x",
                                   source="pull_x_" + source, pulled_by=user)
        finally:
            c.close()
        res["pulled"] = info.get("count", len(items))
        return res
    res = await run_in_threadpool(_do)
    try:
        if res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    code = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=code)


async def api_pull_post(request: Request):
    """Tarik komentar dari SATU postingan tertentu (IG shortcode/URL atau TikTok
    URL/id video). Dipakai tombol "Tarik postingan ini" di Kelola Data.

    IG aman berjalan headless di server; TikTok butuh sesi headed/lokal
    (SOSMED_TT_HEADLESS=0) karena captcha/overlay."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    plat = ((body or {}).get("platform") or "").strip().lower()
    if plat in ("instagram", "ig"):
        plat = "ig"
    elif plat in ("tiktok", "tt"):
        plat = "tiktok"
    post = ((body or {}).get("post") or (body or {}).get("url")
            or (body or {}).get("conversation_id") or "").strip()
    cfg = _POST_PULL.get(plat)
    if not cfg:
        return JSONResponse({"ok": False, "error": "Platform tidak didukung (pilih IG / TikTok)."},
                            status_code=400)
    if not post:
        return JSONResponse({"ok": False, "error": "Postingan (URL/kode) wajib diisi."},
                            status_code=400)
    user = _current_username(request)
    off = _off_handles()

    def _do():
        try:
            coll = importlib.import_module(cfg["module"])
        except Exception as e:
            return {"ok": False, "error": "Collector %s tidak tersedia: %s" % (cfg["label"], e)}
        try:
            items, cinfo = coll.collect_range(official_handles=list(off),
                                              trigger="manual_post",
                                              **{cfg["arg"]: [post]})
        except Exception as e:
            return {"ok": False, "error": "Gagal menarik postingan: %s" % e}
        if not cinfo.get("ok"):
            return {"ok": False, "error": cinfo.get("error", "Gagal menarik postingan."),
                    "need_login": cinfo.get("need_login", False),
                    "need_playwright": cinfo.get("need_playwright", False)}
        c = _conn()
        try:
            res = sdb.ingest_items(c, items, default_platform=plat,
                                   source="pull_post_" + plat, pulled_by=user)
        finally:
            c.close()
        res["pulled"] = cinfo.get("count", len(items))
        return res
    res = await run_in_threadpool(_do)
    try:
        if res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    code = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=code)


async def api_repair(request: Request):
    """Perbaiki data lama: jalankan ulang pairing Q&A sadar-thread untuk SEMUA
    conversation. Dipakai sekali setelah upgrade agar baris lama (yang di-ingest
    versi backend lama) mendapat item_type/status yang benar."""
    def _do():
        c = _conn()
        try:
            return sdb.repair_all_pairing(c)
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


# ---------------------------------------------------------------------------
# Q&A (dulu Inbox / Daftar Q&A)
# ---------------------------------------------------------------------------
def _qp(request, key, default=""):
    return (request.query_params.get(key) or default).strip()


async def api_list(request: Request):
    q = request.query_params
    try:
        limit = int(q.get("limit") or 200)
    except Exception:
        limit = 200
    only_q = (q.get("only_questions") or "").lower() in ("1", "true", "ya", "yes")

    def _do():
        c = _conn()
        try:
            return sdb.list_items(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                topik=_qp(request, "topik"), status=_qp(request, "status"),
                sentiment=_qp(request, "sentiment"),
                item_type=_qp(request, "item_type"),
                handle=_qp(request, "handle"), q=_qp(request, "q"),
                only_questions=only_q, limit=limit)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_thread(request: Request):
    platform = _qp(request, "platform", "x")
    conv = _qp(request, "conversation_id") or _qp(request, "conv")
    if not conv:
        return JSONResponse({"ok": False, "error": "conversation_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            return sdb.get_thread(c, platform, conv)
        finally:
            c.close()
    r = await run_in_threadpool(_do)
    if not r:
        return JSONResponse({"ok": False, "error": "Thread tidak ditemukan."}, status_code=404)
    return JSONResponse(r)


async def api_set_status(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    item_id = (body or {}).get("id")
    status = ((body or {}).get("status") or "").strip()
    if not item_id or status not in sdb.STATUSES:
        return JSONResponse({"ok": False, "error": "id/status tidak valid."}, status_code=400)

    def _do():
        c = _conn()
        try:
            ok = sdb.set_status(c, int(item_id), status)
            return {"ok": ok}
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


async def api_set_topik(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    item_id = (body or {}).get("id")
    topik = ((body or {}).get("topik") or "").strip()
    if not item_id:
        return JSONResponse({"ok": False, "error": "id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            ok = sdb.set_topik(c, int(item_id), topik or None)
            return {"ok": ok}
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


# ---------------------------------------------------------------------------
# Pengawasan SPV (monitoring kurasi manual pertanyaan warga)
# ---------------------------------------------------------------------------
async def api_monitor(request: Request):
    q = request.query_params
    try:
        limit = int(q.get("limit") or 500)
    except Exception:
        limit = 500
    inb = (q.get("include_nimbrung") or "").strip().lower() in ("1", "true", "ya", "yes")

    def _do():
        c = _conn()
        try:
            return smon.monitor_list(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                answered=_qp(request, "answered"), q=_qp(request, "q"),
                include_nimbrung=inb, limit=limit)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_review(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    item_id = (body or {}).get("id")
    if not item_id:
        return JSONResponse({"ok": False, "error": "id wajib."}, status_code=400)
    fields = {}
    for k in ("crm_url", "spv_answered", "spv_answered_at", "spv_answer_link", "spv_note"):
        if k in (body or {}):
            fields[k] = body.get(k)
    user = _current_username(request)

    def _do():
        c = _conn()
        try:
            ok = smon.update_review(c, int(item_id), fields, user=user)
            return {"ok": ok}
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


async def api_post_label(request: Request):
    """Simpan/hapus NAMA postingan (mis. 'P1 Lupa Kata Sandi'). label kosong = hapus."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    plat = ((body or {}).get("platform") or "").strip()
    conv = ((body or {}).get("conversation_id") or (body or {}).get("conv") or "").strip()
    label = ((body or {}).get("label") or "").strip()
    if not conv:
        return JSONResponse({"ok": False, "error": "conversation_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            smon.set_post_label(c, plat, conv, label)
            return {"ok": True, "label": label}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_batch_delete(request: Request):
    """Hapus data satu batch tarikan/impor (Riwayat Impor / Tarik) agar bisa
    ditarik ulang. Menghapus item milik batch itu + baris batch, lalu merajut
    ulang Q&A untuk percakapan yang tersentuh. Item yang pertama kali masuk pada
    batch lain TIDAK ikut terhapus (batch_id item hanya diset saat INSERT)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    bid = ((body or {}).get("batch_id") or "").strip()
    if not bid:
        return JSONResponse({"ok": False, "error": "batch_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            rows = c.execute("SELECT DISTINCT platform, conversation_id "
                             "FROM sosmed_items WHERE batch_id=?", (bid,)).fetchall()
            convs = [(r[0], r[1]) for r in rows]
            n = c.execute("SELECT COUNT(*) FROM sosmed_items WHERE batch_id=?",
                          (bid,)).fetchone()[0]
            c.execute("DELETE FROM sosmed_items WHERE batch_id=?", (bid,))
            c.execute("DELETE FROM sosmed_batches WHERE batch_id=?", (bid,))
            c.commit()
            for (plat, conv) in convs:
                try:
                    sdb._pair_conversation(c, plat, conv)
                except Exception:
                    pass
            c.commit()
            return {"ok": True, "deleted": n, "conversations": len(convs), "batch": bid}
        finally:
            c.close()
    res = await run_in_threadpool(_do)
    try:
        if isinstance(res, dict) and res.get("ok"):
            _kick_reindex_bg()
    except Exception:
        pass
    return JSONResponse(res)


# Kunci meta untuk pengaturan penarikan yang bisa diatur lewat UI.
_CFG_META = {
    "ig_max_posts": ("cfg_ig_max_posts", "SOSMED_IG_MAX_POSTS", 12),
    "tt_max_posts": ("cfg_tt_max_posts", "SOSMED_TT_MAX_VIDEOS", 10),
}

# Peta collector untuk aksi "Tarik postingan ini" (satu postingan tertentu).
_POST_PULL = {
    "ig": {"module": "sosmed.ig_collector", "arg": "only_codes", "label": "Instagram"},
    "tiktok": {"module": "sosmed.tiktok_collector", "arg": "only_urls", "label": "TikTok"},
}


async def api_settings(request: Request):
    """Pengaturan penarikan efektif (maks postingan IG / video TikTok per tarik).
    Nilai meta (>0) menang atas env; bila 0/kosong, pakai env lalu default."""
    def _do():
        c = _conn()
        try:
            out = {"ok": True}
            for bk, (mk, env, dflt) in _CFG_META.items():
                v = sdb.get_meta(c, mk)
                try:
                    iv = int(v) if v not in (None, "") else 0
                except Exception:
                    iv = 0
                if iv > 0:
                    out[bk] = iv
                else:
                    try:
                        out[bk] = int(os.environ.get(env) or dflt)
                    except Exception:
                        out[bk] = int(dflt)
            return out
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_settings_save(request: Request):
    """Simpan pengaturan penarikan (disimpan di sosmed_meta). 0 = pakai default/env."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    def _do():
        c = _conn()
        try:
            saved = {}
            for bk, (mk, _env, _d) in _CFG_META.items():
                if bk in (body or {}):
                    try:
                        val = int(body.get(bk))
                    except Exception:
                        continue
                    if val < 0:
                        val = 0
                    sdb.set_meta(c, mk, val)
                    saved[bk] = val
            return {"ok": True, "saved": saved}
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_monitor_thread(request: Request):
    """Klaster interaksi satu UTAMA (untuk modal ikon mata): utama + tambahan +
    jawaban resmi terkait, plus kandidat komentar resmi (picker jawaban manual)."""
    item_id = _qp(request, "id")
    if not item_id:
        return JSONResponse({"ok": False, "error": "id wajib."}, status_code=400)
    try:
        iid = int(item_id)
    except Exception:
        return JSONResponse({"ok": False, "error": "id tidak valid."}, status_code=400)

    def _do():
        c = _conn()
        try:
            return smon.monitor_thread(c, iid)
        finally:
            c.close()
    r = await run_in_threadpool(_do)
    code = 200 if r.get("ok") else 404
    return JSONResponse(r, status_code=code)


async def api_monitor_posts(request: Request):
    """Ringkasan PER POSTINGAN (untuk verifikasi jumlah data tarikan per post)."""
    q = request.query_params
    try:
        limit = int(q.get("limit") or 300)
    except Exception:
        limit = 300

    def _do():
        c = _conn()
        try:
            return smon.monitor_posts(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                q=_qp(request, "q"), limit=limit)
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_monitor_post(request: Request):
    """SEMUA komentar satu postingan (verifikasi lengkap, bisa dicari Ctrl+F)."""
    platform = _qp(request, "platform")
    conv = _qp(request, "conversation_id") or _qp(request, "conv")
    if not conv:
        return JSONResponse({"ok": False, "error": "conversation_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            return smon.monitor_post(c, platform, conv)
        finally:
            c.close()
    r = await run_in_threadpool(_do)
    code = 200 if r.get("ok") else 404
    return JSONResponse(r, status_code=code)


# ---------------------------------------------------------------------------
# SLA & Analitik (gabungan Coverage & SLA + Analitik)
# ---------------------------------------------------------------------------
async def api_coverage(request: Request):
    def _do():
        c = _conn()
        try:
            return sdb.coverage_sla(c, platform=_qp(request, "platform"),
                                    range_=_qp(request, "range", "all"),
                                    start=_qp(request, "start"), end=_qp(request, "end"))
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


async def api_analytics(request: Request):
    def _do():
        c = _conn()
        try:
            return sdb.analytics(c, platform=_qp(request, "platform"),
                                 range_=_qp(request, "range", "all"),
                                 start=_qp(request, "start"), end=_qp(request, "end"))
        finally:
            c.close()
    return JSONResponse(await run_in_threadpool(_do))


# ---------------------------------------------------------------------------
# Coverage & Deflection (database FAQ + gap pengetahuan bot)
# ---------------------------------------------------------------------------
async def api_knowledge_gap(request: Request):
    q = request.query_params
    try:
        min_count = int(q.get("min_count") or 1)
    except Exception:
        min_count = 1
    use_sem = (q.get("semantic") or "1").lower() not in ("0", "false", "no", "")

    def _do():
        c = _conn()
        try:
            return sk.knowledge_gap(
                c, platform=_qp(request, "platform"),
                range_=_qp(request, "range", "all"),
                start=_qp(request, "start"), end=_qp(request, "end"),
                min_count=min_count, use_semantic=use_sem)
        finally:
            