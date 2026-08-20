# -*- coding: utf-8 -*-
"""rag_reranker.py — Cross-encoder reranker untuk retrieval RAG (Tahap 5).

Retrieval hybrid (FTS5 + bi-encoder) menghasilkan kandidat secara cepat,
tetapi urutannya belum tentu paling relevan. Reranker cross-encoder membaca
pasangan (pertanyaan, kandidat) sekaligus lalu memberi skor relevansi yang jauh
lebih akurat — inilah tahap yang meniru cara mesin pencari modern mengurutkan
"paling relevan -> kurang relevan".

Model default (Fase 0): BAAI/bge-reranker-v2-m3 — reranker multilingual modern,
jauh lebih akurat untuk pasangan pertanyaan–pasal berbahasa Indonesia daripada
mmarco-mMiniLMv2 (2021). Tetap memakai sentence-transformers.CrossEncoder yang
SUDAH menjadi dependency (dipakai embedder), jadi tidak ada paket baru.
Model lama tetap bisa dipakai dengan set env RAG_RERANK_MODEL.

Konfigurasi (env):
  RAG_RERANK          '1' (default) aktif; '0' matikan.
  RAG_RERANK_MODEL    default 'BAAI/bge-reranker-v2-m3'
  RAG_RERANK_POOL     jumlah kandidat maksimum yang dinilai ulang (default 30)
  RAG_RERANK_DEVICE   '' auto (cuda bila ada) / 'cuda' / 'cpu'

Gagal-anggun: bila model/paket tak tersedia atau error, rerank() mengembalikan
urutan asli (dipotong top_k) sehingga retrieval tetap jalan.
"""
import os

_MODEL = None
_MODEL_TRIED = False


def _enabled():
    return str(os.environ.get("RAG_RERANK", "1")).strip().lower() not in ("0", "false", "no", "off")


def model_id():
    return os.environ.get("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")


def _pool():
    try:
        return max(1, int(os.environ.get("RAG_RERANK_POOL", "30")))
    except Exception:
        return 30


def device_info():
    """Diagnostik: perangkat & build torch yang sebenarnya dipakai reranker.
    Berguna untuk membuktikan apakah kode BENAR-BENAR jalan di GPU. Jika
    cuda_available=False padahal PC ber-GPU, biasanya torch yang terinstal
    adalah build CPU-only (lihat catatan di requirements.txt)."""
    info = {"enabled": _enabled(), "model": model_id(),
            "device_env": os.environ.get("RAG_RERANK_DEVICE", "").strip()}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_build"] = getattr(torch.version, "cuda", None)
        info["cuda_available"] = bool(torch.cuda.is_available())
        info["device"] = "cuda" if (not info["device_env"] and info["cuda_available"]) else (info["device_env"] or "cpu")
    except Exception as e:
        info["torch"] = None
        info["error"] = str(e)[:120]
    return info


def _load_model():
    global _MODEL, _MODEL_TRIED
    if _MODEL is not None:
        return _MODEL
    if _MODEL_TRIED:
        return None
    _MODEL_TRIED = True
    if not _enabled():
        return None
    try:
        from sentence_transformers import CrossEncoder
        dev = os.environ.get("RAG_RERANK_DEVICE", "").strip()
        cuda_ok = None
        torch_ver = None
        cuda_build = None
        if not dev:
            try:
                import torch
                cuda_ok = bool(torch.cuda.is_available())
                torch_ver = torch.__version__
                cuda_build = getattr(torch.version, "cuda", None)
                dev = "cuda" if cuda_ok else "cpu"
            except Exception:
                dev = "cpu"
        else:
            try:
                import torch
                cuda_ok = bool(torch.cuda.is_available())
                torch_ver = torch.__version__
                cuda_build = getattr(torch.version, "cuda", None)
            except Exception:
                pass
        _MODEL = CrossEncoder(model_id(), device=dev, max_length=512)
        # Log diagnostik: ungkap perangkat NYATA yang dipakai. Jika device=cpu
        # padahal PC ber-GPU -> hampir pasti torch build CPU-only (cuda_build=None).
        print("[rag_reranker] model=%s device=%s cuda_available=%s torch=%s cuda_build=%s"
              % (model_id(), dev, cuda_ok, torch_ver, cuda_build), flush=True)
    except Exception:
        _MODEL = None
    return _MODEL


def is_available():
    if not _enabled():
        return False
    return _load_model() is not None


def _cand_text(row):
    try:
        d = row if isinstance(row, dict) else dict(row)
    except Exception:
        return ""
    judul = str(d.get("judul") or "").strip()
    isi = str(d.get("isi") or "").strip()
    txt = (judul + ". " + isi).strip(". ").strip()
    return txt[:1800]


def rerank(query, rows, top_k=None):
    """Urutkan ulang `rows` (list dict) berdasar relevansi ke `query`.

    Menambahkan kunci 'rerank_skor' pada tiap baris yang dinilai. Bila model tak
    tersedia, kembalikan rows[:top_k] tanpa perubahan urutan.
    """
    rows = list(rows or [])
    q = (query or "").strip()
    if not rows or not q:
        return rows[:top_k] if top_k else rows
    m = _load_model()
    if m is None:
        return rows[:top_k] if top_k else rows
    pool = rows[:_pool()]
    rest = rows[_pool():]
    try:
        pairs = [(q, _cand_text(r)) for r in pool]
        scores = m.predict(pairs, show_progress_bar=False)
        scored = []
        for r, s in zip(pool, scores):
            try:
                if isinstance(r, dict):
                    r["rerank_skor"] = round(float(s), 4)
            except Exception:
                pass
            scored.append((float(s), r))
        scored.sort(key=lambda x: -x[0])
        ordered = [r for _, r in scored] + rest
    except Exception:
        ordered = rows
    return ordered[:top_k] if top_k else ordered
