# -*- coding: utf-8 -*-
"""pustaka_routes.py — Rute pustaka pengetahuan: Glosarium, Disambiguasi,
Peta Intent (termasuk Katalog Intent & deskripsi AI). Migrasi langkah 3.

Daftarkan dengan:
    import pustaka_routes; pustaka_routes.register(app)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from knowledge import glossary_db as gdb
from knowledge import disambig_db as ddb
from knowledge import intentmap_db as imdb
import common.intent_describe as idesc
from knowledge import stats as pstats
from app_core import render_page


def _enrich_dipakai(items, pustaka):
    """Sisipkan field dipakai (jumlah pemakaian) ke tiap item daftar."""
    try:
        _pc = pstats.init_db(pstats.connect())
        try:
            um = pstats.usage_map(_pc, pustaka)
        finally:
            _pc.close()
    except Exception:
        um = {}
    for it in (items or []):
        try:
            it["dipakai"] = int(um.get(it.get("id"), 0))
        except Exception:
            it["dipakai"] = 0
    return items


async def glossary_page(request: Request):
    return render_page(request, "glossary.html", "glossary")


async def api_glossary_list(request: Request):
    """Daftar istilah (dengan pencarian & filter). Auto-seed contoh saat kosong."""
    q = request.query_params

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            if gdb.count(conn) == 0:
                gdb.seed_defaults(conn)
            items = gdb.list_terms(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                sistem=(q.get("sistem") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "glosarium")
            return {"ok": True, "items": items, "total": gdb.count(conn),
                    "kategori": gdb.KATEGORI, "sistem": gdb.SISTEM, "status": gdb.STATUS}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_glossary_save(request: Request):
    """Tambah atau perbarui satu istilah. Divalidasi di glossary_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            return gdb.upsert_term(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_glossary_delete(request: Request):
    """Hapus satu istilah berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not gid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = gdb.init_db(gdb.connect())
        try:
            return {"ok": gdb.delete_term(conn, gid), "id": gid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def disambig_page(request: Request):
    return render_page(request, "disambig.html", "disambig")


async def api_disambig_list(request: Request):
    """Daftar aturan disambiguasi (dengan pencarian & filter). Auto-seed saat kosong."""
    q = request.query_params

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            if ddb.count(conn) == 0:
                ddb.seed_defaults(conn)
            items = ddb.list_rules(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "disambiguasi")
            return {"ok": True, "items": items, "total": ddb.count(conn),
                    "kategori": ddb.KATEGORI, "sistem": ddb.SISTEM, "status": ddb.STATUS,
                    "default_cutoff": ddb.DEFAULT_CUTOFF}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_disambig_save(request: Request):
    """Tambah atau perbarui satu aturan. Divalidasi di disambig_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            return ddb.upsert_rule(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_disambig_delete(request: Request):
    """Hapus satu aturan berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    rid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not rid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = ddb.init_db(ddb.connect())
        try:
            return {"ok": ddb.delete_rule(conn, rid), "id": rid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def intentmap_page(request: Request):
    return render_page(request, "intentmap.html", "intentmap")


async def api_intentmap_list(request: Request):
    """Daftar kebijakan intent (dengan pencarian & filter). Auto-seed saat kosong."""
    q = request.query_params

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            if imdb.count(conn) == 0:
                imdb.seed_defaults(conn)
            items = imdb.list_intents(
                conn,
                q=(q.get("q") or None),
                kategori=(q.get("kategori") or None),
                struktur=(q.get("struktur") or None),
                status=(q.get("status") or None),
                lang=(q.get("lang") or None),
            )
            _enrich_dipakai(items, "intentmap")
            return {"ok": True, "items": items, "total": imdb.count(conn),
                    "kategori": imdb.KATEGORI, "struktur": imdb.STRUKTUR, "status": imdb.STATUS,
                    "prioritas": imdb.PRIORITAS_LABELS}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_save(request: Request):
    """Tambah atau perbarui satu kebijakan intent. Divalidasi di intentmap_db.validate()."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "Body tidak valid."})

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return imdb.upsert_intent(conn, body)
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_delete(request: Request):
    """Hapus satu kebijakan intent berdasarkan id."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    iid = ((body.get("id") if isinstance(body, dict) else "") or "").strip()
    if not iid:
        return JSONResponse({"ok": False, "error": "id kosong."})

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            return {"ok": imdb.delete_intent(conn, iid), "id": iid}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_catalog(request: Request):
    """Daftar Katalog Intent (deskripsi AI/analis) + statistik ringkas."""
    q = request.query_params

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            items = imdb.catalog_list(conn, q=(q.get("q") or None),
                                      filt=(q.get("filter") or "all"),
                                      lang=(q.get("lang") or None),
                                      limit=imdb._to_int(q.get("limit"), 500))
            _enrich_dipakai(items, "katalog")
            return {"ok": True, "items": items, "stats": imdb.catalog_stats(conn)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_describe(request: Request):
    """Deskripsi AI (draf) utk sebagian intent yg belum dideskripsikan.
    Prioritas: paling sering dipanggil dulu. Batas per batch <=500."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        limit = int(body.get("limit") or 50)
    except Exception:
        limit = 50
    limit = max(1, min(500, limit))
    only_called = bool(body.get("only_called", True))

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            hasil = idesc.run_describe_batch(conn, limit=limit, only_called=only_called)
            return {"ok": True, "hasil": hasil, "stats": imdb.catalog_stats(conn)}
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_approve(request: Request):
    """Setujui/koreksi deskripsi -> terverifikasi (dikunci dari timpa AI)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    iid = ((body.get("id") or "")).strip()
    if not iid:
        return JSONResponse({"ok": False, "error": "id kosong."})
    edits = {}
    for kk in ("deskripsi_maksud", "deskripsi_cakupan"):
        v = body.get(kk)
        if isinstance(v, str) and v.strip():
            edits[kk] = v.strip()
    if isinstance(body.get("sistem_tersinggung"), list):
        edits["sistem_tersinggung"] = body.get("sistem_tersinggung")

    def _run():
        conn = imdb.init_db(imdb.connect())
        try:
            _u = getattr(request.state, "user", None) or {}
            _approver = (_u.get("nama") or _u.get("username") or "").strip()
            res = imdb.approve_description(conn, iid, edits=(edits or None), disetujui_oleh=_approver)
            if isinstance(res, dict):
                res["stats"] = imdb.catalog_stats(conn)
            return res
        finally:
            conn.close()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_describe_start(request: Request):
    """Mulai draf AI latar-belakang (lazy/bertahap) utk SEMUA sisa intent.
    Aman utk ~1.300 intent: berjalan di thread, resumable, tak memblokir request."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    only_called = bool(body.get("only_called", False))
    try:
        chunk = int(body.get("chunk") or 25)
    except Exception:
        chunk = 25
    try:
        max_items = int(body["max_items"]) if body.get("max_items") not in (None, "") else None
    except Exception:
        max_items = None
    try:
        sleep_s = float(body.get("sleep_s") or 0)
    except Exception:
        sleep_s = 0.0

    def _connect():
        return imdb.init_db(imdb.connect())

    def _run():
        res = idesc.start_background_drain(_connect, chunk=chunk, sleep_s=sleep_s,
                                           max_items=max_items, only_called=only_called)
        try:
            conn = _connect()
            try:
                res["stats"] = imdb.catalog_stats(conn)
            finally:
                conn.close()
        except Exception:
            pass
        return res
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_describe_progress(request: Request):
    """Progres job draf AI latar-belakang + statistik katalog terkini."""
    def _run():
        prog = idesc.describe_progress()
        try:
            conn = imdb.init_db(imdb.connect())
            try:
                prog["stats"] = imdb.catalog_stats(conn)
            finally:
                conn.close()
        except Exception:
            pass
        return {"ok": True, "progress": prog}
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_intentmap_describe_stop(request: Request):
    """Minta hentikan job draf AI latar-belakang (berhenti setelah batch berjalan)."""
    def _run():
        return idesc.stop_background_drain()
    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

def register(app):
    # Glosarium
    app.add_api_route("/glossary", glossary_page, methods=["GET"])
    app.add_api_route("/api/glossary/list", api_glossary_list, methods=["GET"])
    app.add_api_route("/api/glossary/save", api_glossary_save, methods=["POST"])
    app.add_api_route("/api/glossary/delete", api_glossary_delete, methods=["POST"])
    # Disambiguasi
    app.add_api_route("/disambig", disambig_page, methods=["GET"])
    app.add_api_route("/api/disambig/list", api_disambig_list, methods=["GET"])
    app.add_api_route("/api/disambig/save", api_disambig_save, methods=["POST"])
    app.add_api_route("/api/disambig/delete", api_disambig_delete, methods=["POST"])
    # Peta Intent + Katalog
    app.add_api_route("/intentmap", intentmap_page, methods=["GET"])
    app.add_api_route("/api/intentmap/list", api_intentmap_list, methods=["GET"])
    app.add_api_route("/api/intentmap/save", api_intentmap_save, methods=["POST"])
    app.add_api_route("/api/intentmap/delete", api_intentmap_delete, methods=["POST"])
    app.add_api_route("/api/intentmap/catalog", api_intentmap_catalog, methods=["GET"])
    app.add_api_route("/api/intentmap/describe", api_intentmap_describe, methods=["POST"])
    app.add_api_route("/api/intentmap/approve", api_intentmap_approve, methods=["POST"])
    app.add_api_route("/api/intentmap/describe/start", api_intentmap_describe_start, methods=["POST"])
    app.add_api_route("/api/intentmap/describe/progress", api_intentmap_describe_progress, methods=["GET"])
    app.add_api_route("/api/intentmap/describe/stop", api_intentmap_describe_stop, methods=["POST"])
