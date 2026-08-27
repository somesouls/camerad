# -*- coding: utf-8 -*-
"""rag/awe_speedup_patch.py — sumber 'awe' pakai index vektor (hasil setara).

Melengkapi rag/sources_speedup_patch.py untuk sumber AWE (Live-Chat Avaya):
mengganti _ctx_awe brute-force (scan LIKE 400 baris transkrip tiap query;
cold-scan ~236 dtk + pemanasan ~232 dtk) dengan pencarian vektor bge-m3
O(1)-query (avaya.semantic_index). Fallback ke brute-force lama bila index
kosong/tak siap / skor di bawah AWE_MIN_COS.

Dipasang dgn 'import rag.awe_speedup_patch' di ekor rag.rerank_patch (setelah
rag.sources_speedup_patch). Gagal-anggun + dpt dimatikan: AWE_INDEX=0 => _ctx_awe
kembali ke brute-force. TIDAK mengubah pilihan sumber (checkbox Konfigurasi
tetap otoritatif) — hanya mempercepat sumber 'awe' bila memang dipilih.

Env: AWE_INDEX(1), AWE_MIN_COS(0.35), AWE_INDEX_POOL(12),
     RAG_WARMUP_EMBED_WAIT_S(120).
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
try:
    import avaya.db as _avdb
except Exception:            # pragma: no cover
    _avdb = None

_ENSURED = {"awe": False}
_AWE_PREV = {"fn": None}


def _on():
    return str(os.environ.get("AWE_INDEX", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _min_cos():
    try:
        return float(os.environ.get("AWE_MIN_COS", "0.35"))
    except Exception:
        return 0.35


def _pool(limit):
    try:
        base = int(os.environ.get("AWE_INDEX_POOL", "12"))
    except Exception:
        base = 12
    return max(int(limit or 3) * 3, base)


def _embed_wait_s():
    try:
        v = float(os.environ.get("RAG_WARMUP_EMBED_WAIT_S", "120"))
    except Exception:
        v = 120.0
    return v if v > 0 else 120.0


def _clip(s, n):
    try:
        return _re._clip(s, n)
    except Exception:
        s = (s or "").strip()
        return s if len(s) <= n else s[:n]


def _cust_and_agent(tx):
    """Pisah pertanyaan pelanggan & jawaban agen dari list {role,text}."""
    cust, agent = [], []
    for m in (tx or []):
        if not isinstance(m, dict):
            continue
        role, text = m.get("role", ""), str(m.get("text", "") or "")
        if not text.strip():
            continue
        try:
            is_ag = _avdb._is_agent(role, text) if _avdb else True
        except Exception:
            is_ag = True
        (agent if is_ag else cust).append(text.strip())
    return cust, agent


def _ctx_awe_v2(q, limit=3):
    prev = _AWE_PREV.get("fn")

    def _fb():
        return prev(q, limit) if callable(prev) else ("", [])

    asi = sys.modules.get("avaya.semantic_index")
    if asi is None or not _on():
        return _fb()
    try:
        hits = asi.search_ids(q, k=_pool(limit))
    except Exception:
        hits = []
    floor = _min_cos()
    hits = [(sid, sc) for (sid, sc) in (hits or []) if sc >= floor]
    if not hits:
        return _fb()
    if _avdb is None:
        return _fb()
    try:
        c = _avdb.init_db(_avdb.connect())
    except Exception:
        return _fb()
    blocks, sources = [], []
    try:
        for sid, _sc in hits[:int(limit or 3)]:
            try:
                d = _avdb.get_transcript(c, sid)
            except Exception:
                d = None
            if not d or not d.get("transkrip"):
                continue
            cust, agent = _cust_and_agent(d.get("transkrip"))
            if not cust and not agent:
                continue
            label = (d.get("jenis_layanan") or "").strip() or "Percakapan AWE"
            blok = ("Topik: %s\nPertanyaan pelanggan: %s\nJawaban agen: %s"
                    % (label, _clip(" ".join(cust), 300),
                       _clip(" ".join(agent), 600)))
            blocks.append(blok)
            sources.append({"sumber": "Data AWE (Live-Chat)", "judul": label,
                            "ref": str(sid), "url": ""})
    finally:
        try:
            c.close()
        except Exception:
            pass
    if not blocks:
        return _fb()
    return "\n\n".join(blocks), sources


def _ensure_awe_index():
    if _ENSURED["awe"] or not _on():
        return
    if _re is None or not isinstance(getattr(_re, "_DISPATCH", None), dict):
        return
    asi = sys.modules.get("avaya.semantic_index")
    if asi is None:
        try:
            import avaya.semantic_index as asi  # noqa: F401
        except Exception:
            asi = None
    if asi is None:
        return
    cur = _re._DISPATCH.get("awe")
    if cur is _ctx_awe_v2:
        _ENSURED["awe"] = True
        return
    _AWE_PREV["fn"] = cur
    _re._DISPATCH["awe"] = _ctx_awe_v2
    try:
        _re._ctx_awe = _ctx_awe_v2
    except Exception:
        pass
    _ENSURED["awe"] = True
    try:
        print("[rag_awe_speedup] index awe aktif (fallback brute-force bila kosong).",
              flush=True)
    except Exception:
        pass


def _wait_embed_ready(timeout_s=120.0):
    try:
        import peraturan.semantic as _psem
    except Exception:
        return False
    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        try:
            if _psem.is_available() and _psem.embed_query("pemanasan") is not None:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def reindex(force=False):
    """Bangun ulang index AWE (dipanggil manual/endpoint). Fail-open."""
    try:
        import avaya.semantic_index as asi
        return asi.build(force=force)
    except Exception as e:
        return {"ok": False, "n": 0, "reason": str(e)[:120]}


def _prewarm():
    if not _on():
        return
    for _ in range(180):
        if (_re is not None
                and isinstance(getattr(_re, "_DISPATCH", None), dict)
                and _avdb is not None):
            break
        time.sleep(0.5)
    _ensure_awe_index()
    ready = _wait_embed_ready(_embed_wait_s())
    try:
        import avaya.semantic_index as asi
        r = asi.build()
        print("[rag_awe_speedup] build index awe: %s (embed_ready=%s)"
              % (r, ready), flush=True)
    except Exception as e:
        try:
            print("[rag_awe_speedup] build index awe dilewati:", e, flush=True)
        except Exception:
            pass


def _install():
    try:
        _ensure_awe_index()
    except Exception:
        pass
    if _on():
        try:
            threading.Thread(target=_prewarm, daemon=True).start()
        except Exception:
            pass
    try:
        print("[rag_awe_speedup] aktif (AWE_INDEX=%s, MIN_COS=%s)."
              % (_on(), _min_cos()), flush=True)
    except Exception:
        pass


_install()
