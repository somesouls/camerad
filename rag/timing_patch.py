# -*- coding: utf-8 -*-
"""rag_timing_patch.py — instrumentasi waktu per-tahap mesin RAG (diagnostik).

TUJUAN: menunjukkan JALUR MANA yang lambat/macet secara KONKRET tanpa mengubah
logika mesin sedikit pun. Hanya membungkus (wrap) tiga titik dengan pencatatan
waktu lalu mencetak ke log:

  1. rag_engine._retrieve_one  -> waktu retrieval PER SUMBER (intent/peraturan/…)
  2. rag_engine.answer         -> waktu TOTAL satu jawaban + rekap retrieval
  3. common.llm_client.chat    -> waktu tiap panggilan LLM (verifikasi/sintesis)

Semua wrapper meneruskan argumen apa adanya (*args, **kwargs) sehingga TIDAK
mungkin mengubah perilaku; setiap galat internal pencatatan diabaikan
(gagal-anggun). Nilai kembalian selalu = nilai asli fungsi yang dibungkus.

Aktif via env RAG_TIMING (default '1'); set RAG_TIMING=0 untuk mematikan.
Dipasang otomatis saat diimpor (dipicu dari rag/rerank_patch.py, karena di titik
itu rag_engine sudah termuat oleh rag_successor_patch).
"""
import os
import time
import threading

try:
    import rag.engine as _eng
except Exception:            # pragma: no cover
    _eng = None

try:
    import common.llm_client as _llm
except Exception:            # pragma: no cover
    _llm = None

_TLS = threading.local()
_INSTALLED = False


def _on():
    return str(os.environ.get("RAG_TIMING", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _install():
    global _INSTALLED
    if _INSTALLED or _eng is None:
        return
    _INSTALLED = True

    _orig_retrieve_one = _eng._retrieve_one
    _orig_answer = _eng.answer

    def _retrieve_one(*a, **k):
        if not _on():
            return _orig_retrieve_one(*a, **k)
        key = a[0] if a else k.get("key", "?")
        t0 = time.monotonic()
        n = 0
        try:
            t, s = _orig_retrieve_one(*a, **k)
            try:
                n = len(s or [])
            except Exception:
                n = 0
            return t, s
        finally:
            dt = time.monotonic() - t0
            acc = getattr(_TLS, "acc", None)
            if isinstance(acc, list):
                acc.append((str(key), round(dt, 3), n))
            try:
                print("[rag_timing] retrieval sumber=%s %.3fs hits=%d"
                      % (str(key), dt, n), flush=True)
            except Exception:
                pass

    def _answer(*a, **k):
        if not _on():
            return _orig_answer(*a, **k)
        _TLS.acc = []
        t0 = time.monotonic()
        try:
            prof = a[1] if len(a) > 1 else k.get("profile", {})
        except Exception:
            prof = {}
        pid = str((prof or {}).get("id") or "")
        try:
            q = (a[0] if a else k.get("question", "")) or ""
        except Exception:
            q = ""
        try:
            return _orig_answer(*a, **k)
        finally:
            dt = time.monotonic() - t0
            parts = getattr(_TLS, "acc", None) or []
            retr = " ".join("%s=%.2fs(%d)" % (kk, d, n) for kk, d, n in parts)
            try:
                print("[rag_timing] TOTAL answer=%.3fs profil=%s retrieval[%s] q=%r"
                      % (dt, pid, retr, str(q)[:60]), flush=True)
            except Exception:
                pass
            _TLS.acc = None

    _eng._retrieve_one = _retrieve_one
    _eng.answer = _answer

    if _llm is not None:
        _orig_chat = _llm.chat

        def _chat(*a, **k):
            if not _on():
                return _orig_chat(*a, **k)
            t0 = time.monotonic()
            ok = True
            try:
                return _orig_chat(*a, **k)
            except Exception:
                ok = False
                raise
            finally:
                dt = time.monotonic() - t0
                try:
                    print("[rag_timing] llm.chat %.3fs ok=%s" % (dt, ok),
                          flush=True)
                except Exception:
                    pass

        _llm.chat = _chat

    try:
        print("[rag_timing_patch] aktif (RAG_TIMING=%s) — waktu per-tahap RAG dicatat."
              % os.environ.get("RAG_TIMING", "1"), flush=True)
    except Exception:
        pass


_install()
