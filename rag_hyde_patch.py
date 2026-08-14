# -*- coding: utf-8 -*-
"""
rag_hyde_patch.py — Pasang HyDE pada jalur retrieval DENSE, per-profil.

Cara kerja
----------
1. Membungkus rag_engine.answer(question, profile, ...) untuk MENANDAI (thread-
   local) apakah HyDE boleh aktif pada request ini, berdasar:
     - env RAG_HYDE (master switch, default NONAKTIF), dan
     - profil: hanya profil yang tercantum di RAG_HYDE_PROFILES (default
       'agent'). Chatbot cepat TIDAK terkena tambahan latensi LLM.
2. Membungkus peraturan_db._vec_ids(conn, query, limit) — titik tunggal jalur
   DENSE (dipakai oleh peraturan_db.search maupun _split_search dari patch
   rerank). Bila tanda HyDE aktif, query diubah jadi dokumen hipotetis via
   rag_hyde.untuk_dense. Jalur LEXICAL (FTS5/_fts_ids) TIDAK diubah -> kata
   kunci asli tetap dipakai.

Kenapa membungkus _vec_ids (bukan search): agar HyDE berlaku HANYA pada sisi
dense/semantik, kompatibel dengan base search maupun dense/lexical split, dan
tidak mengganggu skor lexical/RRF.

Aman: fail-open penuh (galat apa pun -> perilaku persis seperti tanpa patch).
Idempoten (tak memasang ulang). Modul ini di-load lewat rag_grounding_patch
sehingga tak perlu menyentuh web_app.py.
"""
import os
import threading

import peraturan_db as _pdb

try:
    import rag_engine as _re
except Exception:
    _re = None

try:
    import rag_hyde
except Exception:
    rag_hyde = None


_TLS = threading.local()


def _master_on():
    v = os.environ.get("RAG_HYDE")
    if v is None:
        return False
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _profiles():
    raw = os.environ.get("RAG_HYDE_PROFILES", "agent")
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _profile_id(profile):
    try:
        if isinstance(profile, dict):
            return str(profile.get("id") or profile.get("nama") or "").strip().lower()
        return str(profile or "").strip().lower()
    except Exception:
        return ""


def _install():
    if rag_hyde is None:
        print("[rag_hyde_patch] rag_hyde tak tersedia; lewati.", flush=True)
        return
    if getattr(_pdb, "_hyde_patched", False):
        return

    # (1) bungkus _vec_ids: transformasi query bila tanda HyDE aktif
    _orig_vec = _pdb._vec_ids

    def _vec_ids_hyde(conn, query, limit=50):
        q = query
        try:
            if getattr(_TLS, "on", False):
                nq = rag_hyde.untuk_dense(query)
                if nq:
                    q = nq
        except Exception:
            q = query
        return _orig_vec(conn, q, limit)

    _pdb._vec_ids = _vec_ids_hyde
    _pdb._hyde_patched = True

    # (2) bungkus answer: set tanda per-profil (bila rag_engine tersedia)
    if _re is not None and not getattr(_re, "_hyde_answer_patched", False):
        _orig_answer = _re.answer

        def _answer_hyde(question, profile, override=None, history=None,
                         diagnostics=False):
            prev = getattr(_TLS, "on", False)
            try:
                _TLS.on = bool(_master_on() and _profile_id(profile) in _profiles())
            except Exception:
                _TLS.on = False
            try:
                return _orig_answer(question, profile, override=override,
                                    history=history, diagnostics=diagnostics)
            finally:
                _TLS.on = prev

        _re.answer = _answer_hyde
        _re._hyde_answer_patched = True

    print("[rag_hyde_patch] HyDE terpasang (master=%s, profil=%s)."
          % (_master_on(), ",".join(sorted(_profiles()))), flush=True)


_install()
