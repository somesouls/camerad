# -*- coding: utf-8 -*-
"""rag_routes.py — Halaman & API mesin RAG (chat produksi + backend form uji).

Rute:
  GET  /rag                  -> ChatBot Pajak untuk Wajib Pajak (profil 'chatbot')
  POST /api/rag/chat         -> jawab chat produksi
  POST /api/rag/lab          -> backend uji on-demand — dipakai form \"Uji Cepat\"
                                di /rag-chatbot & /rag-agent (halaman /rag-lab
                                sendiri sudah DIHAPUS v22)
  GET  /api/rag/profiles     -> daftar profil (admin)
  POST /api/rag/profile      -> ambil satu profil {id} (admin)
  POST /api/rag/profile/save -> simpan profil/prompt (admin)

Mesin inti ada di rag_engine.py; profil/prompt/chip di rag_config_db.py.
Gating akses diatur di app_core (_route_area): /rag & /api/rag/chat = area
'common' (semua pengguna login), sisanya area 'peraturan' (admin).

Daftarkan dengan:  import rag_routes; rag_routes.register(app)
"""
import json
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app_core import render_page

import rag.engine as rag_engine
import rag.config_db as rcfg
import rag.status_routes as rag_status_routes


async def _body(request: Request):
    try:
        raw = await request.body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# --------------------------------------------------------------------------
# Halaman
# --------------------------------------------------------------------------
async def page_rag(request: Request):
    return render_page(request, "rag.html", "rag")


# --------------------------------------------------------------------------
# API chat produksi
# --------------------------------------------------------------------------
async def api_rag_chat(request: Request):
    body = await _body(request)
    question = (body.get("question") or "").strip()
    history = body.get("history") if isinstance(body.get("history"), list) else []
    if not question and history:
        for h in reversed(history):
            if isinstance(h, dict) and (h.get("role") or "").lower() == "user":
                question = (h.get("content") or "").strip()
                break
    if not question:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    try:
        res = await run_in_threadpool(rag_engine.jawab_chat, question, history, "chatbot")
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# --------------------------------------------------------------------------
# API backend form uji (dipakai /rag-chatbot & /rag-agent)
# --------------------------------------------------------------------------
async def api_rag_lab(request: Request):
    body = await _body(request)
    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "Pertanyaan kosong."})
    profil = (body.get("profil") or "chatbot").strip()
    sumber = body.get("sumber")
    if not isinstance(sumber, list):
        sumber = None
    history = body.get("history") if isinstance(body.get("history"), list) else []
    # Opsi \"mode produksi\": jalankan uji mengikuti mode NYATA profil (mis.
    # chatbot = cepat/tanpa loop verifikasi) alih-alih memaksa pipeline penuh.
    prod_mode = bool(body.get("prod_mode"))
    try:
        res = await run_in_threadpool(
            rag_engine.jawab_lab, question, profil, sumber, history, prod_mode)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


async def api_profiles(request: Request):
    try:
        return JSONResponse({
            "ok": True,
            "profil": rcfg.list_profiles(),
            "sumber_valid": list(rcfg.SUMBER_VALID),
            "sumber_label": rcfg.SUMBER_LABEL,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


async def api_profile_get(request: Request):
    body = await _body(request)
    pid = (body.get("id") or "").strip()
    p = rcfg.get_profile(pid) if pid else None
    if not p:
        return JSONResponse({"ok": False, "error": "Profil tak ditemukan."}, status_code=404)
    return JSONResponse({"ok": True, "profil": p})


async def api_profile_save(request: Request):
    body = await _body(request)
    pid = (body.get("id") or "").strip()
    if not pid:
        return JSONResponse({"ok": False, "error": "id profil wajib."}, status_code=400)
    try:
        p = await run_in_threadpool(rcfg.save_profile, pid, body)
        return JSONResponse({"ok": True, "profil": p})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _warmup_intent_semantic():
    """Pramuat seluruh artefak berat mesin RAG di latar belakang saat boot:
    indeks semantik intent, model reranker, model embedding korpus (bge-m3),
    MATRIKS vektor peraturan (puluhan MB blob -> numpy), dan matriks indeks
    Q&A historis. Tanpa ini, artefak-matriks dimuat MALAS di request pertama
    pasca-restart — jawaban bisa melampaui batas waktu proxy/domain publik
    (edge menjawab halaman HTML -> UI menampilkan 'Unexpected token <').
    Nonaktif via RAG_INTENT_SEMANTIC_WARMUP=0 (khusus indeks semantik).
    Gagal-anggun: kegagalan apa pun hanya dicatat, tidak menghentikan server."""
    if str(os.environ.get("RAG_INTENT_SEMANTIC_WARMUP", "1")).strip().lower() in (
            "0", "false", "no", "off"):
        return

    def _bg():
        try:
            import rag.intent_semantic as ris
            ris.warmup()
            print("[warmup] indeks semantik intent siap.", flush=True)
        except Exception as e:
            try:
                print("[warmup] rag_intent_semantic dilewati:", e, flush=True)
            except Exception:
                pass
        # Pramuat model reranker cross-encoder (bila aktif, env RAG_RERANK) agar
        # query pertama tak menanggung waktu muat model. is_available() akan
        # memicu pemuatan model bila belum dimuat.
        try:
            import rag.reranker as rag_reranker
            if rag_reranker.is_available():
                print("[warmup] model reranker cross-encoder siap.", flush=True)
            else:
                print("[warmup] reranker nonaktif/tak tersedia (dilewati).", flush=True)
        except Exception as e:
            try:
                print("[warmup] rag_reranker dilewati:", e, flush=True)
            except Exception:
                pass
        # Pramuat model embedding korpus (bge-m3): request RAG pertama memicu
        # lazy-load yang bisa puluhan detik; bila klien lewat proxy bertimeout,
        # request diputus sebelum jawaban terkirim (UI: \"Gagal terhubung\").
        # Muat model + satu embed pemanasan di sini agar request pertama cepat.
        try:
            import peraturan.semantic as psem
            if psem.is_available():
                try:
                    psem.embed_query("pemanasan")
                except Exception:
                    pass
                print("[warmup] model embedding korpus (bge-m3) siap.", flush=True)
            else:
                print("[warmup] embedding korpus nonaktif/tak tersedia (dilewati).", flush=True)
        except Exception as e:
            try:
                print("[warmup] peraturan_semantic dilewati:", e, flush=True)
            except Exception:
                pass
        # v27: pramuat MATRIKS vektor peraturan (33 ribu+ blob -> numpy,
        # puluhan MB dari SQLite) + matriks indeks Q&A historis. Keduanya
        # lazy-load di pencarian pertama; satu pencarian kecil di sini memicu
        # muat penuhnya saat boot, bukan saat request pengguna pertama.
        try:
            import peraturan.db as pdb
            pdb.search("pemanasan", 1, ("berlaku",))
            print("[warmup] matriks vektor peraturan siap.", flush=True)
        except Exception as e:
            try:
                print("[warmup] matriks peraturan dilewati:", e, flush=True)
            except Exception:
                pass
        try:
            import db.qa_index_db as _qa
            _qa.search("pemanasan", k=1)
            print("[warmup] matriks indeks Q&A siap.", flush=True)
        except Exception as e:
            try:
                print("[warmup] indeks Q&A dilewati:", e, flush=True)
            except Exception:
                pass

    try:
        threading.Thread(target=_bg, name="warmup-intent-semantic", daemon=True).start()
    except Exception:
        pass


def register(app):
    app.add_api_route("/rag", page_rag, methods=["GET"])
    app.add_api_route("/api/rag/chat", api_rag_chat, methods=["POST"])
    # Catatan v22: halaman /rag-lab DIHAPUS. API /api/rag/lab DIPERTAHANKAN —
    # ia adalah backend form \"Uji Cepat\" di /rag-chatbot & /rag-agent.
    app.add_api_route("/api/rag/lab", api_rag_lab, methods=["POST"])
    app.add_api_route("/api/rag/profiles", api_profiles, methods=["GET"])
    app.add_api_route("/api/rag/profile", api_profile_get, methods=["POST"])
    app.add_api_route("/api/rag/profile/save", api_profile_save, methods=["POST"])
    # Endpoint status mesin (read-only, admin) untuk kartu Status di halaman ini.
    rag_status_routes.register(app)
    # Warm-up semua artefak berat (model + matriks vektor) saat didaftarkan.
    _warmup_intent_semantic()
