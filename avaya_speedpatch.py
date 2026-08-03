# -*- coding: utf-8 -*-
"""
AVAYA speed patch v2 - drop-in, build-agnostic, hemat GPU & resumable.

Pakai SETELAH modul avaya aktif (setelah %run llm_fix_final_combined.py):
    import avaya_speedpatch
    avaya_speedpatch.apply()

Perbaikan dibanding v1:
1. Reranker BGE jadi SINGLETON  -> tidak dimuat dua kali (hemat ~2.3GB GPU RAM).
2. Reranker fp16 (half)          -> ~2x lebih cepat + separuh VRAM (AVAYA_RR_FP16=0 utk mati).
3. max_length 512 -> 256         -> lebih cepat (AVAYA_RR_MAXLEN).
4. top-k kandidat 8 -> 4          -> jauh lebih sedikit pasang direrank (AVAYA_RR_TOPK).
5. Seleksi kandidat divektorkan  -> tidak ada loop python O(n_dok) per percakapan.
6. CHECKPOINT ke Drive           -> reranking tahan-disconnect; re-run melanjutkan,
                                    tidak mengulang dari nol.
7. Bebas memori antar-tahap      -> hanya 1 model embedding aktif pada satu waktu.

Semua dibungkus fallback: bila optimasi gagal, otomatis kembali ke jalur asli
(lambat tapi benar).

Env opsional:
  AVAYA_RR_TOPK   (default 4)    kandidat intent yang direrank per percakapan
  AVAYA_RR_BATCH  (default 128)  pasang per batch reranker
  AVAYA_RR_MAXLEN (default 256)  panjang token maksimum reranker
  AVAYA_RR_FP16   (default 1)    1=half precision, 0=fp32
  AVAYA_CKPT_EVERY(default 10)   simpan checkpoint tiap N batch reranker
"""
import os, json, hashlib

_TOPK      = int(os.environ.get("AVAYA_RR_TOPK", "4"))
_RR_BATCH  = int(os.environ.get("AVAYA_RR_BATCH", "128"))
_MAXLEN    = int(os.environ.get("AVAYA_RR_MAXLEN", "256"))
_USE_FP16  = os.environ.get("AVAYA_RR_FP16", "1") == "1"
_CKPT_EVERY= int(os.environ.get("AVAYA_CKPT_EVERY", "10"))

_MATCH_CACHE = {}
_RERANKER_SINGLETON = None
_ORIG_MAKE_RERANKER = None


def _atomic_save(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print("[AVAYA-SPEED] gagal simpan checkpoint: %r" % e, flush=True)


def _ckpt_path(queries):
    base = os.environ.get("PIPELINE_RUNS_DIR") or os.environ.get("PIPELINE_WORKDIR") or "/tmp"
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        base = "/tmp"
    sig = hashlib.md5(("\n".join(sorted(set(q or "" for q in queries)))).encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(base, "avaya_mapping_%s.json" % sig)


def _optimized_make_reranker():
    """Reranker BGE singleton + fp16. Fallback ke bawaan bila gagal."""
    global _RERANKER_SINGLETON
    if _RERANKER_SINGLETON is not None:
        return _RERANKER_SINGLETON
    import avaya_pipeline as ap
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        mid = ap.RERANKER_MODEL_ID
        tok = AutoTokenizer.from_pretrained(mid)
        use_cuda = torch.cuda.is_available()
        dtype = torch.float16 if (use_cuda and _USE_FP16) else torch.float32
        mdl = AutoModelForSequenceClassification.from_pretrained(mid, torch_dtype=dtype)
        mdl.eval()
        if use_cuda:
            mdl = mdl.cuda()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass

        def _rerank(pairs):
            if not pairs:
                return []
            with torch.no_grad():
                enc = tok([p[0] for p in pairs], [p[1] for p in pairs],
                          padding=True, truncation=True, max_length=_MAXLEN, return_tensors="pt")
                if use_cuda:
                    enc = {k: v.cuda() for k, v in enc.items()}
                logits = mdl(**enc).logits.view(-1).float()
                return torch.sigmoid(logits).cpu().tolist()
        _RERANKER_SINGLETON = _rerank
        print("[AVAYA-SPEED] reranker siap: dtype=%s cuda=%s max_len=%d" % (dtype, use_cuda, _MAXLEN), flush=True)
        return _rerank
    except Exception as e:
        print("[AVAYA-SPEED] reranker optimized gagal (%r) -> pakai bawaan." % e, flush=True)
        _RERANKER_SINGLETON = _ORIG_MAKE_RERANKER() if _ORIG_MAKE_RERANKER else None
        return _RERANKER_SINGLETON


def _batch_match_all(catalog, q_emb, queries, reranker, progress=None, checkpoint_path=None):
    """Meniru IntentCatalog.match untuk semua query, tapi reranker DI-BATCH,
    kandidat divektorkan, dan hasil di-checkpoint (resumable)."""
    import numpy as np
    emb = catalog._emb
    doc_ids = catalog._doc_ids
    doc_texts = catalog._doc_texts
    title_of = getattr(catalog, "intent_title", {}) or {}
    N = len(queries)
    if emb is None or not doc_ids:
        return [("", "", 0.0)] * N

    results = [None] * N
    done = {}
    if checkpoint_path and os.path.isfile(checkpoint_path):
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                done = json.load(f)
        except Exception:
            done = {}

    todo = []
    for i, q in enumerate(queries):
        r = done.get(q)
        if r is not None:
            results[i] = (r[0], r[1], float(r[2]))
        else:
            todo.append(i)
    if progress:
        progress("Memuat checkpoint pemetaan", N - len(todo), N)

    if todo:
        emb = np.asarray(emb, dtype=np.float32)
        uniq = list(dict.fromkeys(doc_ids))
        iid_to_k = {iid: k for k, iid in enumerate(uniq)}
        doc_k = np.asarray([iid_to_k[i] for i in doc_ids], dtype=np.int64)
        n_int = len(uniq)
        intent_docs = [np.where(doc_k == k)[0] for k in range(n_int)]
        topk = min(_TOPK, n_int)

        all_pairs = []
        spans = []  # (qi, start, end, [iid...], cos_iid, cos_score)
        CH = 256
        for cs in range(0, len(todo), CH):
            idxs = todo[cs:cs + CH]
            Qc = np.stack([np.asarray(q_emb[i], dtype=np.float32) for i in idxs])  # (c,dim)
            sims = emb @ Qc.T  # (n_docs, c)
            best = np.full((n_int, len(idxs)), -1e30, dtype=np.float32)
            np.maximum.at(best, doc_k, sims)
            if topk < n_int:
                part = np.argpartition(-best, topk - 1, axis=0)[:topk]  # (topk, c)
            else:
                part = np.tile(np.arange(n_int)[:, None], (1, len(idxs)))
            for jj, qi in enumerate(idxs):
                col = best[:, jj]
                ks = part[:, jj]
                ks = ks[np.argsort(-col[ks], kind="stable")]
                start = len(all_pairs)
                for k in ks:
                    dd = intent_docs[int(k)]
                    bd = int(dd[int(np.argmax(sims[dd, jj]))])
                    all_pairs.append((queries[qi], doc_texts[bd]))
                spans.append((qi, start, len(all_pairs),
                              [uniq[int(k)] for k in ks],
                              uniq[int(ks[0])], float(col[int(ks[0])])))
            if progress:
                progress("Menyiapkan kandidat intent", min(cs + CH, len(todo)), len(todo))

        scores = [0.0] * len(all_pairs)
        if reranker is not None and all_pairs:
            span_ptr = 0
            nb = 0
            for s in range(0, len(all_pairs), _RR_BATCH):
                seg = reranker(all_pairs[s:s + _RR_BATCH])
                for t, v in enumerate(seg):
                    scores[s + t] = float(v)
                processed = min(s + _RR_BATCH, len(all_pairs))
                while span_ptr < len(spans) and spans[span_ptr][2] <= processed:
                    qi, st, en, iids, _ci, _cscore = spans[span_ptr]
                    seg2 = scores[st:en]
                    bi = int(np.argmax(np.asarray(seg2)))
                    r = (iids[bi], title_of.get(iids[bi], iids[bi]), float(seg2[bi]))
                    results[qi] = r
                    done[queries[qi]] = [r[0], r[1], r[2]]
                    span_ptr += 1
                nb += 1
                if progress:
                    progress("Reranking intent (batched)", processed, len(all_pairs))
                if checkpoint_path and (nb % _CKPT_EVERY == 0):
                    _atomic_save(checkpoint_path, done)
            if checkpoint_path:
                _atomic_save(checkpoint_path, done)
        else:
            for qi, st, en, iids, ci, cscore in spans:
                results[qi] = (ci, title_of.get(ci, ci), cscore)
                done[queries[qi]] = [ci, title_of.get(ci, ci), cscore]
            if checkpoint_path:
                _atomic_save(checkpoint_path, done)

    for i in range(N):
        if results[i] is None:
            results[i] = ("", "", 0.0)
    return results


def progress_report():
    """Info manual: berapa banyak query yang sudah ter-checkpoint di Drive."""
    base = os.environ.get("PIPELINE_RUNS_DIR") or "/tmp"
    try:
        files = [f for f in os.listdir(base) if f.startswith("avaya_mapping_") and f.endswith(".json")]
    except Exception:
        files = []
    for f in files:
        try:
            with open(os.path.join(base, f), encoding="utf-8") as fh:
                print("%s -> %d query ter-checkpoint" % (f, len(json.load(fh))), flush=True)
        except Exception:
            pass
    if not files:
        print("Belum ada checkpoint di %s" % base, flush=True)


def apply():
    global _ORIG_MAKE_RERANKER
    import avaya_pipeline as ap
    if getattr(ap, "_SPEEDPATCH_APPLIED", False):
        print("[AVAYA-SPEED] sudah terpasang.", flush=True)
        return

    # 1) reranker singleton + fp16
    _ORIG_MAKE_RERANKER = ap.make_reranker
    ap.make_reranker = _optimized_make_reranker

    # 2) match cache
    _orig_match = ap.IntentCatalog.match

    def _match_patched(self, query_emb, reranker=None, query_text=None):
        if reranker is not None and query_text is not None and query_text in _MATCH_CACHE:
            return _MATCH_CACHE[query_text]
        return _orig_match(self, query_emb, reranker=reranker, query_text=query_text)
    ap.IntentCatalog.match = _match_patched

    # 3) memoisasi encode (mpnet: instance-independent)
    _orig_encode = ap.EmbeddingBackend.encode
    _ENC_MEMO = {}

    def _encode_memo(self, texts):
        if getattr(self, "mode", "") != "mpnet":
            return _orig_encode(self, texts)
        try:
            key = (len(texts), hashlib.md5(("\x00".join((t if t else " ") for t in texts)).encode("utf-8", "replace")).hexdigest())
        except Exception:
            return _orig_encode(self, texts)
        if key in _ENC_MEMO:
            return _ENC_MEMO[key]
        res = _orig_encode(self, texts)
        if len(_ENC_MEMO) < 6:
            _ENC_MEMO[key] = res
        return res
    ap.EmbeddingBackend.encode = _encode_memo

    # 4) bungkus run_pipeline: prefill (batched + checkpoint) lalu pipeline asli
    _orig_run_pipeline = ap.run_pipeline

    def _run_pipeline_fast(json_payloads, training_src, intent_src, qwen_ctx=None,
                           prefer_transformer=True, progress=None):
        _MATCH_CACHE.clear()
        try:
            def _p(stage, done=None, total=None):
                if progress:
                    try:
                        progress(stage, done, total)
                    except Exception:
                        pass
            convs = ap.merge_conversations(json_payloads)
            if convs:
                _p("Pra-hitung pemetaan intent (batched)", 0, len(convs))
                catalog = ap.IntentCatalog().load(training_src, intent_src)
                if hasattr(catalog, "resolve_types"):
                    try:
                        catalog.resolve_types()
                    except Exception:
                        pass
                backend = ap.EmbeddingBackend(prefer_transformer=prefer_transformer)
                reranker = ap.make_reranker() if prefer_transformer else None
                if reranker is not None:
                    queries = [ap.extract_customer_query(c) for c in convs]
                    if getattr(backend, "mode", "") in ("bow", "tfidf"):
                        try:
                            backend.fit_corpus(catalog.build_docs() + queries + list(catalog.intent_title.values()))
                        except Exception:
                            pass
                    catalog.embed(backend)
                    q_emb = backend.encode(queries)
                    ckpt = _ckpt_path(queries)
                    res = _batch_match_all(catalog, q_emb, queries, reranker, progress=_p, checkpoint_path=ckpt)
                    for qt, r in zip(queries, res):
                        _MATCH_CACHE[qt] = r
                    print("[AVAYA-SPEED] pra-hitung selesai: %d chat, %d intent di-cache (ckpt=%s)" % (len(convs), len(_MATCH_CACHE), os.path.basename(ckpt)), flush=True)
                    # bebaskan memori embedding sebelum pipeline asli jalan
                    try:
                        del backend, catalog, q_emb, res
                        import gc; gc.collect()
                        import torch; torch.cuda.empty_cache()
                    except Exception:
                        pass
        except Exception as _e:
            print("[AVAYA-SPEED] pra-hitung gagal (%r) -> fallback jalur normal." % _e, flush=True)
            _MATCH_CACHE.clear()
        try:
            return _orig_run_pipeline(json_payloads, training_src, intent_src,
                                      qwen_ctx=qwen_ctx, prefer_transformer=prefer_transformer,
                                      progress=progress)
        finally:
            _MATCH_CACHE.clear()

    ap.run_pipeline = _run_pipeline_fast
    ap._SPEEDPATCH_APPLIED = True
    print("[AVAYA-SPEED] v2 terpasang | topk=%d batch=%d maxlen=%d fp16=%s ckpt_every=%d" % (_TOPK, _RR_BATCH, _MAXLEN, _USE_FP16, _CKPT_EVERY), flush=True)


if __name__ == "__main__":
    apply()
