# -*- coding: utf-8 -*-
"""rag_sources_speedup_patch.py — percepat & amankan retrieval (hasil tetap sama).

Diimpor di ujung rag.rerank_patch, TEPAT setelah rag.timing_patch (sehingga
anggaran waktu membungkus wrapper timing). Empat hal, semua gagal-anggun & dapat
dimatikan lewat env:

  1. CACHE NORMALISASI. _ctx_sosmed_v2/_ctx_awe_v2 (rag.sources_patch) memanggil
     _norm_hay() untuk SETIAP baris (hingga 2000 FAQ) pada SETIAP query. Hasil
     normalisasi hanya bergantung teks baris (bukan query) -> di-cache per-teks.
     Query ke-2 dst. atas korpus sama jadi <1 dtk. Hasil retrieval SAMA PERSIS.

  2. PREWARM. Di thread latar, panaskan cache normalisasi sosmed sekali setelah
     startup agar query PERTAMA tidak kena biaya stemming 'dingin' (sumber
     sosmed sempat 185 dtk pada query pertama).

  3. GUARD SOP KOSONG. Bila tabel sop_unit kosong, sop.db.search selalu 0 hasil
     tapi tetap meng-embed query di GPU + memicu AI-rewrite. Guard melewatinya
     bila tabel benar-benar kosong. Fail-open: ragu -> jalan normal.

  4. ANGGARAN WAKTU KOOPERATIF (RAG_BUDGET_S, default 75 dtk). answer() menandai
     tenggat; sumber yang BELUM mulai dilewati (kembalikan ("", [])) begitu
     tenggat lewat -> answer() pasti selesai sehingga polling frontend tidak
     macet/looping. TIDAK menghentikan sumber yang SEDANG berjalan (profil Agent
     boleh lama sampai peraturan tuntas).

Env:
  RAG_SOURCES_SPEEDUP=0  -> matikan cache normalisasi + prewarm + guard SOP.
  RAG_BUDGET_S=0         -> matikan anggaran waktu (default 75).

Modul ini TIDAK mengubah pilihan sumber tiap profil (tetap diatur di menu
Konfigurasi). Ia hanya mempercepat & membatasi waktu, tanpa mengubah hasil.
"""
import os
import sys
import time
import threading

try:
    import rag.engine as _re
except Exception:            # pragma: no cover
    _re = None


def _flag_on(name, default="1"):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "off")


def _budget_s():
    try:
        v = float(os.environ.get("RAG_BUDGET_S", "75"))
    except Exception:
        v = 75.0
    return v if v > 0 else 0.0


# ===================================================== 1) cache normalisasi
_NORM_CACHE = {}
_NORM_CACHE_MAKS = 200000
_ENSURED = {"norm": False, "sop": False}


def _ensure_norm_cache():
    if _ENSURED["norm"] or not _flag_on("RAG_SOURCES_SPEEDUP"):
        return
    sp = sys.modules.get("rag.sources_patch")
    if sp is None or not hasattr(sp, "_norm_hay"):
        return  # sources_patch belum termuat; dicoba lagi nanti
    _orig = sp._norm_hay
    if getattr(_orig, "_camerad_cached", False):
        _ENSURED["norm"] = True
        return

    def _norm_hay_cached(text):
        key = text if isinstance(text, str) else (text or "")
        v = _NORM_CACHE.get(key)
        if v is not None:
            return v
        try:
            v = _orig(text)
        except Exception:
            v = (text or "").lower()
        if len(_NORM_CACHE) < _NORM_CACHE_MAKS:
            _NORM_CACHE[key] = v
        return v

    _norm_hay_cached._camerad_cached = True
    sp._norm_hay = _norm_hay_cached
    _ENSURED["norm"] = True
    try:
        print("[rag_sources_speedup] cache normalisasi aktif.", flush=True)
    except Exception:
        pass


# ===================================================== 2) prewarm sosmed
def _prewarm():
    if not _flag_on("RAG_SOURCES_SPEEDUP"):
        return
    for _ in range(180):     # tunggu maks ~90 dtk sampai startup siap
        if (sys.modules.get("rag.sources_patch") is not None
                and _re is not None and getattr(_re, "sdb", None) is not None):
            break
        time.sleep(0.5)
    _ensure_installed()
    sp = sys.modules.get("rag.sources_patch")
    sdb = getattr(_re, "sdb", None) if _re is not None else None
    if sp is None or sdb is None or not hasattr(sp, "_norm_hay"):
        return
    try:
        c = sdb.init_db(sdb.connect())
        try:
            fp = sdb.faq_pairs(c, only_answered=True, limit=2000)
            pairs = fp.get("pairs") or []
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        pairs = []
    n = 0
    for p in pairs:
        try:
            hay = ((p.get("pertanyaan") or "") + " " + str(p.get("topik") or ""))
            sp._norm_hay(hay)
            n += 1
        except Exception:
            pass
    try:
        print("[rag_sources_speedup] prewarm normalisasi sosmed: %d baris." % n,
              flush=True)
    except Exception:
        pass


# ===================================================== 3) guard SOP kosong
_SOP_EMPTY = {"t": 0.0, "v": None}
_SOP_TTL = 30.0


def _sop_kosong(sopdb):
    now = time.monotonic()
    if _SOP_EMPTY["v"] is not None and (now - _SOP_EMPTY["t"]) < _SOP_TTL:
        return _SOP_EMPTY["v"]
    kosong = False
    try:
        c = sopdb.init_db(sopdb.connect())
        try:
            n = c.execute("SELECT COUNT(*) FROM sop_unit").fetchone()[0]
            kosong = (int(n or 0) == 0)
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        kosong = False       # fail-open
    _SOP_EMPTY["t"] = now
    _SOP_EMPTY["v"] = kosong
    return kosong


def _ensure_sop_guard():
    if _ENSURED["sop"] or not _flag_on("RAG_SOURCES_SPEEDUP"):
        return
    sopdb = sys.modules.get("sop.db")
    if sopdb is None or not hasattr(sopdb, "search"):
        return
    _orig = sopdb.search
    if getattr(_orig, "_camerad_guarded", False):
        _ENSURED["sop"] = True
        return

    def _search_guarded(*a, **k):
        try:
            if _sop_kosong(sopdb):
                return []
        except Exception:
            pass
        return _orig(*a, **k)

    _search_guarded._camerad_guarded = True
    sopdb.search = _search_guarded
    _ENSURED["sop"] = True
    try:
        print("[rag_sources_speedup] guard SOP kosong aktif.", flush=True)
    except Exception:
        pass


def _ensure_installed():
    try:
        _ensure_norm_cache()
    except Exception:
        pass
    try:
        _ensure_sop_guard()
    except Exception:
        pass


# ===================================================== 4) anggaran waktu
_TLS = threading.local()


def _install_budget():
    if _re is None or getattr(_re, "_camerad_budget_patched", False):
        return
    orig_answer = getattr(_re, "answer", None)
    orig_retrieve = getattr(_re, "_retrieve_one", None)
    if not callable(orig_answer) or not callable(orig_retrieve):
        return

    def answer_budget(*a, **k):
        _ensure_installed()
        b = _budget_s()
        try:
            _TLS.deadline = (time.monotonic() + b) if b > 0 else None
        except Exception:
            _TLS.deadline = None
        try:
            return orig_answer(*a, **k)
        finally:
            try:
                _TLS.deadline = None
            except Exception:
                pass

    def retrieve_budget(*a, **k):
        try:
            dl = getattr(_TLS, "deadline", None)
            if dl is not None and time.monotonic() > dl:
                src = (a[0] if a else k.get("key") or k.get("sumber")) or "?"
                try:
                    print("[rag_timing] SKIP sumber=%s (lewat anggaran %.0fs)"
                          % (src, _budget_s()), flush=True)
                except Exception:
                    pass
                return ("", [])
        except Exception:
            pass
        return orig_retrieve(*a, **k)

    _re.answer = answer_budget
    _re._retrieve_one = retrieve_budget
    _re._camerad_budget_patched = True


# ===================================================== pemasangan
def _install():
    if _budget_s() > 0:
        try:
            _install_budget()
        except Exception as e:
            try:
                print("[rag_sources_speedup] budget dilewati:", e, flush=True)
            except Exception:
                pass
    _ensure_installed()
    if _flag_on("RAG_SOURCES_SPEEDUP"):
        try:
            threading.Thread(target=_prewarm, daemon=True).start()
        except Exception:
            pass
    try:
        print("[rag_sources_speedup] aktif (RAG_SOURCES_SPEEDUP=%s, RAG_BUDGET_S=%s)."
              % (_flag_on("RAG_SOURCES_SPEEDUP"), _budget_s()), flush=True)
    except Exception:
        pass


_install()
