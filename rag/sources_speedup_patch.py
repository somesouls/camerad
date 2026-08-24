# -*- coding: utf-8 -*-
"""rag/sources_speedup_patch.py — percepat & amankan retrieval (hasil setara).

Diimpor di ujung rag.rerank_patch, TEPAT setelah rag.timing_patch. Semua fitur
gagal-anggun + dapat dimatikan lewat env:

  1. CACHE NORMALISASI  — _norm_hay per-teks di-cache (dipakai awe & fallback sosmed).
  2. PREWARM            — panaskan cache + BANGUN index vektor sosmed di latar.
  3. GUARD SOP KOSONG   — lewati sop.db.search bila tabel sop_unit kosong.
  4. ANGGARAN + GATE GPU — answer() menandai tenggat; sumber yang BELUM mulai
     dilewati begitu tenggat lewat; retrieval berat diserialkan semaphore GPU
     (anti thundering-herd saat cold-start + storm /api/chat/detect).
  5. INDEX SOSMED       — _ctx_sosmed memakai pencarian vektor bge-m3 O(1)-query
     (sosmed.semantic_index); fallback ke brute-force lama bila index kosong/
     tak siap / skor di bawah SOSMED_MIN_COS.
  6. URUTAN SUMBER      — sumber berat (default: awe) dijalankan PALING AKHIR
     dengan me-reorder keluaran router. TIDAK mengubah PILIHAN sumber (checkbox
     Konfigurasi tetap otoritatif) — hanya urutan eksekusi, agar peraturan tak
     keburu keskip anggaran gara-gara awe (245 dtk) jalan lebih dulu.

Env: RAG_SOURCES_SPEEDUP(1), RAG_BUDGET_S(75), RAG_GPU_GATE(1),
     RAG_GPU_GATE_WAIT_S(90), RAG_HEAVY_SOURCES(awe), SOSMED_INDEX(1),
     SOSMED_MIN_COS(0.35), RAG_RERANK_POOL(30).
"""
import os
import sys
import time
import threading

try:
    import rag.engine as _re
except Exception:            # pragma: no cover
    _re = None
try:
    import rag.reranker as _rr
except Exception:            # pragma: no cover
    _rr = None


def _flag_on(name, default="1"):
    return str(os.environ.get(name, default)).strip().lower() not in (
        "0", "false", "no", "off")


def _budget_s():
    try:
        v = float(os.environ.get("RAG_BUDGET_S", "75"))
    except Exception:
        v = 75.0
    return v if v > 0 else 0.0


def _idx_on():
    return _flag_on("RAG_SOURCES_SPEEDUP") and _flag_on("SOSMED_INDEX")


def _pool_size(k):
    try:
        base = int(os.environ.get("RAG_RERANK_POOL", "30"))
    except Exception:
        base = 30
    return max(int(k or 10), base)


def _sosmed_limit():
    try:
        return int(os.environ.get("SOSMED_INDEX_LIMIT", "100000") or 100000)
    except Exception:
        return 100000


def _min_cos():
    try:
        return float(os.environ.get("SOSMED_MIN_COS", "0.35"))
    except Exception:
        return 0.35


def _gpu_n():
    try:
        return int(os.environ.get("RAG_GPU_GATE", "1"))
    except Exception:
        return 1


def _gpu_wait():
    try:
        return float(os.environ.get("RAG_GPU_GATE_WAIT_S", "90"))
    except Exception:
        return 90.0


def _heavy():
    raw = os.environ.get("RAG_HEAVY_SOURCES", "awe")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


# ===================================================== 1) cache normalisasi
_NORM_CACHE = {}
_NORM_CACHE_MAKS = 200000
_ENSURED = {"norm": False, "sop": False, "sosmed": False}


def _ensure_norm_cache():
    if _ENSURED["norm"] or not _flag_on("RAG_SOURCES_SPEEDUP"):
        return
    sp = sys.modules.get("rag.sources_patch")
    if sp is None or not hasattr(sp, "_norm_hay"):
        return
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


# ===================================================== 5) index sosmed (v3)
_SOSMED_PREV = {"fn": None}


def _ctx_sosmed_v3(q, limit=3):
    prev = _SOSMED_PREV.get("fn")

    def _fb():
        return prev(q, limit) if callable(prev) else ("", [])

    six = sys.modules.get("sosmed.semantic_index")
    if six is None or not _idx_on():
        return _fb()
    try:
        hits = six.search_ids(q, k=_pool_size(limit))
    except Exception:
        hits = []
    floor = _min_cos()
    hits = [(pid, sc) for (pid, sc) in (hits or []) if sc >= floor]
    if not hits:
        return _fb()
    sdb = getattr(_re, "sdb", None)
    if sdb is None:
        return _fb()
    try:
        c = sdb.init_db(sdb.connect())
        try:
            fp = sdb.faq_pairs(c, only_answered=True, limit=_sosmed_limit())
            pairs = fp.get("pairs") or []
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        pairs = []
    by_id = {}
    for p in pairs:
        try:
            by_id[int(p.get("id"))] = p
        except Exception:
            pass
    cand = []
    for pid, _sc in hits:
        p = by_id.get(int(pid))
        if not p or not (p.get("jawaban_draf") or "").strip():
            continue
        label = p.get("topik") or "Data Sosmed"
        blok = ("Topik: %s\nPertanyaan: %s\nJawaban resmi: %s"
                % (label, _re._clip(p.get("pertanyaan"), 300),
                   _re._clip(p.get("jawaban_draf"), 500)))
        cand.append({"judul": str(label), "isi": blok, "_p": p, "_blok": blok})
    if not cand:
        return _fb()
    if _rr is not None and len(cand) > 1:
        try:
            if _rr.is_available():
                cand = _rr.rerank(q, cand, top_k=None)
        except Exception:
            pass
    blocks, sources = [], []
    for citem in cand[:int(limit or 3)]:
        p = citem["_p"]
        try:
            url = _re._sosmed_url(p)
        except Exception:
            url = str(p.get("permalink") or "")
        blocks.append(citem["_blok"])
        sources.append({"sumber": "Data Sosmed", "judul": citem["judul"],
                        "ref": str(p.get("platform") or "").upper(), "url": url})
    return "\n\n".join(blocks), sources


def _ensure_sosmed_index():
    if _ENSURED["sosmed"] or not _idx_on():
        return
    if _re is None or not isinstance(getattr(_re, "_DISPATCH", None), dict):
        return
    six = sys.modules.get("sosmed.semantic_index")
    if six is None:
        try:
            import sosmed.semantic_index as six  # noqa: F401
        except Exception:
            six = None
    if six is None:
        return
    cur = _re._DISPATCH.get("sosmed")
    if cur is _ctx_sosmed_v3:
        _ENSURED["sosmed"] = True
        return
    _SOSMED_PREV["fn"] = cur
    _re._DISPATCH["sosmed"] = _ctx_sosmed_v3
    try:
        _re._ctx_sosmed = _ctx_sosmed_v3
    except Exception:
        pass
    _ENSURED["sosmed"] = True
    try:
        print("[rag_sources_speedup] index sosmed aktif (fallback brute-force bila kosong).",
              flush=True)
    except Exception:
        pass


# ===================================================== 2) prewarm + build index
def _prewarm():
    if not _flag_on("RAG_SOURCES_SPEEDUP"):
        return
    for _ in range(180):
        if (sys.modules.get("rag.sources_patch") is not None
                and _re is not None and getattr(_re, "sdb", None) is not None):
            break
        time.sleep(0.5)
    _ensure_installed()
    sp = sys.modules.get("rag.sources_patch")
    sdb = getattr(_re, "sdb", None) if _re is not None else None
    if sp is not None and sdb is not None and hasattr(sp, "_norm_hay"):
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
                sp._norm_hay((p.get("pertanyaan") or "") + " "
                             + str(p.get("topik") or ""))
                n += 1
            except Exception:
                pass
        try:
            print("[rag_sources_speedup] prewarm normalisasi sosmed: %d baris." % n,
                  flush=True)
        except Exception:
            pass
    if _idx_on():
        try:
            import sosmed.semantic_index as six
            r = six.build()
            print("[rag_sources_speedup] build index sosmed: %s" % (r,), flush=True)
        except Exception as e:
            try:
                print("[rag_sources_speedup] build index sosmed dilewati:", e, flush=True)
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
        kosong = False
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
    for fn in (_ensure_norm_cache, _ensure_sop_guard, _ensure_sosmed_index):
        try:
            fn()
        except Exception:
            pass


# ===================================================== 4) anggaran + gate GPU
_TLS = threading.local()
_GPU_SEM = None


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
        # (a) lewati sumber yang BELUM mulai bila tenggat sudah lewat
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
        # (b) gate GPU: serialkan retrieval berat; fail-open ke jalan bila
        #     tak dapat izin dalam sisa anggaran (tak pernah nyangkut/keskip).
        sem = _GPU_SEM
        if sem is None:
            return orig_retrieve(*a, **k)
        got = False
        try:
            wait = _gpu_wait()
            dl = getattr(_TLS, "deadline", None)
            if dl is not None:
                wait = max(0.0, min(wait, dl - time.monotonic()))
            got = sem.acquire(timeout=wait) if wait > 0 else sem.acquire(blocking=False)
        except Exception:
            got = False
        try:
            return orig_retrieve(*a, **k)
        finally:
            if got:
                try:
                    sem.release()
                except Exception:
                    pass

    _re.answer = answer_budget
    _re._retrieve_one = retrieve_budget
    _re._camerad_budget_patched = True


# ===================================================== 6) urutan sumber berat
def _install_router_reorder():
    try:
        import rag.router as _rt
    except Exception:
        return
    if getattr(_rt, "_camerad_reorder_patched", False):
        return
    orig = getattr(_rt, "route", None)
    if not callable(orig):
        return

    def _route(q, allowed, *a, **k):
        r = orig(q, allowed, *a, **k)
        try:
            heavy = _heavy()
            ordered = r.get("ordered") if isinstance(r, dict) else None
            if heavy and isinstance(ordered, list):
                back = [s for s in ordered if s in heavy]
                if back:
                    r["ordered"] = [s for s in ordered if s not in heavy] + back
        except Exception:
            pass
        return r

    _rt.route = _route
    _rt._camerad_reorder_patched = True
    try:
        print("[rag_sources_speedup] urutan sumber: %s dijalankan paling akhir."
              % ",".join(sorted(_heavy())), flush=True)
    except Exception:
        pass


# ===================================================== pemasangan
def _install():
    global _GPU_SEM
    try:
        n = _gpu_n()
        _GPU_SEM = threading.BoundedSemaphore(n) if n and n > 0 else None
    except Exception:
        _GPU_SEM = None
    if _budget_s() > 0:
        try:
            _install_budget()
        except Exception as e:
            try:
                print("[rag_sources_speedup] budget dilewati:", e, flush=True)
            except Exception:
                pass
    try:
        if _heavy():
            _install_router_reorder()
    except Exception as e:
        try:
            print("[rag_sources_speedup] reorder dilewati:", e, flush=True)
        except Exception:
            pass
    _ensure_installed()
    if _flag_on("RAG_SOURCES_SPEEDUP"):
        try:
            threading.Thread(target=_prewarm, daemon=True).start()
        except Exception:
            pass
    try:
        print("[rag_sources_speedup] aktif (SPEEDUP=%s, BUDGET_S=%s, GPU_GATE=%s, "
              "HEAVY=%s, SOSMED_INDEX=%s)."
              % (_flag_on("RAG_SOURCES_SPEEDUP"), _budget_s(), _gpu_n(),
                 ",".join(sorted(_heavy())) or "-", _flag_on("SOSMED_INDEX")),
              flush=True)
    except Exception:
        pass


_install()
