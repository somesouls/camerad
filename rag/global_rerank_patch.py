# -*- coding: utf-8 -*-
"""rag/global_rerank_patch.py — PR D: rerank GLOBAL lintas-sumber + chunking
peraturan saat kueri.

Masalah yang diperbaiki
-----------------------
rag_engine._assemble(keys, cache, q) merakit konteks dengan cara:
  - memanggil retrieval per-sumber sesuai URUTAN ROUTER,
  - menyambung blok per-sumber apa adanya, lalu
  - MEMOTONG MENTAH di MAKS_KONTEKS (6500 char).
Akibatnya tidak ada penilaian relevansi LINTAS-SUMBER: blok peraturan yang
sangat relevan bisa terpotong hanya karena berada di urutan belakang, sementara
blok intent/sosmed yang lemah tetap menempati konteks. Inilah akar keluhan
"sumber yang dilempar ke LLM sebagian masih kurang tepat".

Perbaikan (aditif, fail-open)
-----------------------------
Membungkus _assemble sehingga:
  1) Mengumpulkan SEMUA blok kandidat dari sumber yang diizinkan.
  2) Memecah blok panjang (mis. pasal peraturan) menjadi CHUNK per-ayat/kalimat
     SAAT KUERI (tanpa reindex DB); tiap chunk tetap membawa baris header
     (mis. "Peraturan: ... - Pasal N") agar tetap bisa dipahami & dikutip.
  3) MERERANK semua chunk dengan cross-encoder (rag.reranker) thd pertanyaan.
  4) Memilih chunk teratas secara serakah di bawah BUDGET karakter, lalu
     merakit ulang konteks dikelompokkan per label sumber.
Sumber (untuk sitasi) yang dikembalikan hanya sumber yang chunk-nya lolos —
selaras & saling menguatkan dengan PR A (filter sitasi 'hanya yang dirujuk').

Gagal-anggun total: bila reranker tak tersedia / error / hasil kosong / jumlah
chunk < 2, kembalikan hasil _assemble ASLI. Matikan paksa via RAG_GLOBAL_RERANK=0.

Env:
  RAG_GLOBAL_RERANK   (default 1)     aktif/nonaktif fitur.
  RAG_GLOBAL_BUDGET   (default MAKS_KONTEKS) budget karakter konteks akhir.
  RAG_GLOBAL_CHUNK_MIN(default 900)   blok > nilai ini dipecah per-ayat/kalimat.
  RAG_GLOBAL_MAX_UNITS(default 64)    batas chunk yang dinilai reranker (latensi).
"""
import os
import re

import rag.engine as _re

try:
    import rag.reranker as _rr
except Exception:            # pragma: no cover
    _rr = None


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _on():
    return _truthy(os.environ.get("RAG_GLOBAL_RERANK", "1"))


def _env_int(name, default):
    try:
        v = os.environ.get(name)
        if v is None or str(v).strip() == "":
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _budget():
    return _env_int("RAG_GLOBAL_BUDGET",
                    int(getattr(_re, "MAKS_KONTEKS", 6500) or 6500))


def _chunk_min():
    return _env_int("RAG_GLOBAL_CHUNK_MIN", 900)


def _max_units():
    return _env_int("RAG_GLOBAL_MAX_UNITS", 64)


def _split_blocks(text):
    return [b.strip() for b in (text or "").split("\n\n") if b and b.strip()]


_AYAT_RE = re.compile(r"(?<=\S)\s*(?=\(\d{1,2}\)\s)")
_SENT_RE = re.compile(r"(?<=[.;])\s+(?=[A-Z(])")


def _subchunks(block, chunk_min):
    """Pecah blok panjang jadi beberapa chunk <= chunk_min char; tiap chunk
    diberi prefix baris header blok agar konteksnya tetap jelas & bisa dikutip."""
    b = (block or "").strip()
    if len(b) <= chunk_min:
        return [b]
    lines = b.split("\n")
    header = lines[0].strip()
    body = b[len(lines[0]):].lstrip("\n")
    parts = _AYAT_RE.split(body)
    if len(parts) < 2:
        parts = _SENT_RE.split(body)
    if len(parts) < 2:
        parts = [body[i:i + chunk_min] for i in range(0, len(body), chunk_min)]
    chunks, cur = [], ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if cur and (len(cur) + len(p) + 1) > chunk_min:
            chunks.append(cur.strip())
            cur = p
        else:
            cur = (cur + " " + p).strip() if cur else p
    if cur.strip():
        chunks.append(cur.strip())
    out = []
    for ch in chunks:
        if header and not ch.startswith(header):
            out.append(header + "\n" + ch)
        else:
            out.append(ch)
    return out or [b]


def _rr_available():
    if _rr is None:
        return False
    try:
        return bool(_rr.is_available())
    except Exception:
        return False


_orig_assemble = getattr(_re, "_assemble", None)


def _assemble_global(keys, cache, q):
    # Fail-open cepat: fungsi asli hilang / fitur mati / reranker absen.
    if _orig_assemble is None:
        return "", []
    try:
        if not _on() or not _rr_available():
            return _orig_assemble(keys, cache, q)

        rcfg = getattr(_re, "rcfg", None)
        if rcfg is not None:
            def label_of(k):
                try:
                    return rcfg.SUMBER_LABEL.get(k, k)
                except Exception:
                    return k
        else:
            def label_of(k):
                return k
        cmin = _chunk_min()

        units = []  # {label, key, text, srcs:[...]}
        for key in keys:
            if key not in cache:
                cache[key] = _re._retrieve_one(key, q)
            t, s = cache.get(key, ("", []))
            if not (t and str(t).strip()):
                continue
            label = label_of(key)
            blocks = _split_blocks(t)
            s = list(s or [])
            if len(blocks) == len(s) and len(blocks) >= 1:
                pairs = [(blocks[i], [s[i]]) for i in range(len(blocks))]
            else:
                # Tak selaras (mis. blok kebijakan intent) -> satu unit utuh.
                pairs = [(str(t).strip(), s)]
            for btext, bsrcs in pairs:
                for ch in _subchunks(btext, cmin):
                    units.append({"label": label, "key": key,
                                  "text": ch, "srcs": bsrcs})

        if len(units) < 2:
            return _orig_assemble(keys, cache, q)

        mu = _max_units()
        if len(units) > mu:
            units = units[:mu]

        rows = [{"judul": u["label"], "isi": u["text"], "_u": u} for u in units]
        try:
            ordered = _rr.rerank(q, rows, top_k=None) or []
        except Exception:
            return _orig_assemble(keys, cache, q)
        ordered_units = [r["_u"] for r in ordered
                         if isinstance(r, dict) and r.get("_u") is not None]
        if not ordered_units:
            return _orig_assemble(keys, cache, q)

        budget = _budget()
        chosen, total = [], 0
        seen_src, chosen_srcs = set(), []
        for u in ordered_units:
            add = len(u["text"]) + 2
            if chosen and (total + add) > budget:
                continue
            chosen.append(u)
            total += add
            for sc in (u["srcs"] or []):
                if not isinstance(sc, dict):
                    continue
                k = (sc.get("sumber", ""), sc.get("judul", ""))
                if k not in seen_src:
                    seen_src.add(k)
                    chosen_srcs.append(sc)
            if total >= budget:
                break

        if not chosen:
            return _orig_assemble(keys, cache, q)

        # Rakit ulang, kelompokkan per label sumber sesuai urutan kemunculan.
        order_labels, by_label = [], {}
        for u in chosen:
            if u["label"] not in by_label:
                by_label[u["label"]] = []
                order_labels.append(u["label"])
            by_label[u["label"]].append(u["text"])
        parts = []
        for i, lab in enumerate(order_labels, 1):
            parts.append("### Sumber %d - %s\n%s"
                         % (i, lab, "\n\n".join(by_label[lab])))
        body = "\n\n".join(parts)
        maks = int(getattr(_re, "MAKS_KONTEKS", 0) or 0)
        if maks and len(body) > maks:
            body = body[:maks].rstrip() + "\u2026"
        return body, chosen_srcs
    except Exception:
        try:
            return _orig_assemble(keys, cache, q)
        except Exception:
            return "", []


if _orig_assemble is not None and not getattr(_re, "_global_rerank_patched", False):
    _re._assemble = _assemble_global
    _re._global_rerank_patched = True
