# -*- coding: utf-8 -*-
"""
eval_retrieval.py — Evaluasi kualitas RETRIEVAL peraturan (recall@k, MRR, hit@k).

Kenapa file ini ada
-------------------
Harness evaluasi yang sudah ada (eval_harness.py + eval_db.py + /rag-eval)
menilai JAWABAN akhir end-to-end lewat LLM-judge (benar/salah/halusinasi/
abstain). Itu penting, tapi TIDAK memberi tahu apakah kegagalan berasal dari
(a) RETRIEVAL yang tak menemukan pasal yang tepat, atau (b) LLM yang salah
merangkai konteks yang sebenarnya sudah benar.

File ini menutup lubang itu: ia mengukur langsung mutu retrieval terhadap
sebuah "gold set" — daftar pertanyaan yang sudah kita ketahui pasal/peraturan
jawabannya. Metrik retrieval adalah prediktor terkuat mutu RAG: kalau pasal
yang benar tidak masuk ke top-k, LLM sebagus apa pun tak akan bisa menjawab
dengan benar.

Yang diukur
-----------
- hit@k    : proporsi pertanyaan yang punya >=1 unit gold di top-k.
- recall@k : rata-rata (unit gold tertangkap di top-k / total unit gold),
             dibatasi maksimum 1.0 per query (aproksimatif).
- MRR      : mean reciprocal rank dari unit gold PERTAMA yang ditemukan.

Pakai ini untuk menjawab pertanyaan nyata:
  * "Apakah query rewriting benar-benar membantu?" -> bandingkan
    --query-mode raw  vs  --query-mode rewrite
  * "Apakah patch retrieval (dense/lexical split, xref) menaikkan recall?"
    -> jalankan dengan --patches rag_rerank_patch lalu bandingkan.
  * "Apakah contextual chunking (reindex_context.py) menaikkan recall?"
    -> ukur recall SEBELUM lalu SESUDAH menjalankan reindex_context.py.

Gold set (JSONL) — satu objek JSON per baris:
  {"pertanyaan": "npwp ku ilang gimana ngurusnya",
   "gold": [{"jenis": "PER", "nomor": "PER-04/PJ/2020", "pasal": "10"}]}
  {"pertanyaan": "...", "gold_ids": ["<id-unit-persis>"]}
Field:
  - pertanyaan : string pertanyaan user (boleh informal/slang/typo).
  - gold       : (opsional) daftar {jenis, nomor, pasal?}; pasal boleh kosong
                 untuk mencocokkan di level peraturan.
  - gold_ids   : (opsional) daftar id unit persis (kolom peraturan_unit.id).
  - minimal salah satu dari gold / gold_ids harus ada.
Baris tanpa "pertanyaan" (mis. {"_comment": "..."}) dilewati otomatis.

Contoh pemakaian:
  python eval_retrieval.py --goldset eval_retrieval_goldset.jsonl
  python eval_retrieval.py --query-mode raw
  python eval_retrieval.py --query-mode rewrite
  python eval_retrieval.py --patches rag_rerank_patch --out laporan.json

Aman: TIDAK mengubah DB, TIDAK mengubah perilaku engine. Hanya membaca.
"""
import os
import re
import sys
import json
import argparse

import peraturan_db as pdb

# rag_rewrite opsional; hanya dipakai bila --query-mode rewrite.
try:
    import rag_rewrite
except Exception:
    rag_rewrite = None


DEFAULT_KS = (1, 3, 5, 10)


def _norm(s):
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _load_goldset(path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
            except Exception as e:
                sys.stderr.write("Lewati baris %d (JSON invalid): %s\n" % (ln, e))
                continue
            if not isinstance(obj, dict) or not obj.get("pertanyaan"):
                continue
            if not (obj.get("gold") or obj.get("gold_ids")):
                sys.stderr.write("Lewati baris %d (tanpa gold/gold_ids)\n" % ln)
                continue
            items.append(obj)
    return items


def _prep_query(q, mode):
    if mode == "rewrite":
        if rag_rewrite is None:
            raise RuntimeError("rag_rewrite tidak tersedia untuk --query-mode rewrite")
        try:
            return rag_rewrite.untuk_retrieval(q) or q
        except Exception:
            return q
    return q


def _row_matches(row, item):
    ids = set(item.get("gold_ids") or [])
    if ids and row.get("id") in ids:
        return True
    for g in (item.get("gold") or []):
        if _norm(row.get("jenis_peraturan")) != _norm(g.get("jenis")):
            continue
        if _norm(row.get("nomor")) != _norm(g.get("nomor")):
            continue
        gp = g.get("pasal")
        if gp in (None, ""):
            return True  # cocok di level peraturan
        if _norm(row.get("pasal")) == _norm(gp):
            return True
    return False


def _gold_size(item):
    n = len(item.get("gold_ids") or []) + len(item.get("gold") or [])
    return max(1, n)


def evaluate(goldset, ks=DEFAULT_KS, query_mode="rewrite", topn=None,
             status_list=("berlaku",)):
    topn = topn or max(ks)
    per_q = []
    for item in goldset:
        q0 = item["pertanyaan"]
        q = _prep_query(q0, query_mode)
        try:
            rows = pdb.search(q, k=topn, status_list=status_list)
        except Exception as e:
            rows = []
            sys.stderr.write("search() gagal utk %r: %s\n" % (q0, e))
        match_ranks = [i for i, r in enumerate(rows, start=1) if _row_matches(r, item)]
        per_q.append({
            "pertanyaan": q0,
            "query_terpakai": (q if q != q0 else None),
            "first_rank": (match_ranks[0] if match_ranks else None),
            "match_ranks": match_ranks,
            "gold_size": _gold_size(item),
            "n_hasil": len(rows),
        })

    n = len(per_q) or 1
    summary = {"n_query": len(per_q), "query_mode": query_mode, "per_k": {}}
    mrr = sum((1.0 / r["first_rank"]) for r in per_q if r["first_rank"]) / n
    summary["mrr"] = round(mrr, 4)
    for k in ks:
        hit = sum(1 for r in per_q if r["first_rank"] and r["first_rank"] <= k)
        rec = sum(min(1.0, sum(1 for mr in r["match_ranks"] if mr <= k) / r["gold_size"])
                  for r in per_q) / n
        summary["per_k"][k] = {"hit_rate": round(hit / n, 4), "recall": round(rec, 4)}
    return summary, per_q


def _apply_patches(names):
    for nm in names:
        nm = nm.strip()
        if not nm:
            continue
        try:
            __import__(nm)
            sys.stderr.write("[patch] %s diterapkan\n" % nm)
        except Exception as e:
            sys.stderr.write("[patch] gagal impor %s: %s\n" % (nm, e))


def _print_summary(summary):
    print("=" * 60)
    print("EVALUASI RETRIEVAL  (mode query: %s)" % summary["query_mode"])
    print("Jumlah query : %d" % summary["n_query"])
    print("MRR          : %.4f" % summary["mrr"])
    print("-" * 60)
    print("%-6s %-12s %-12s" % ("k", "hit_rate", "recall"))
    for k, v in summary["per_k"].items():
        print("%-6d %-12.4f %-12.4f" % (k, v["hit_rate"], v["recall"]))
    print("=" * 60)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Evaluasi retrieval peraturan (recall@k, MRR, hit@k).")
    ap.add_argument("--goldset", default="eval_retrieval_goldset.jsonl",
                    help="Path JSONL gold set (fallback: eval_retrieval_goldset.example.jsonl).")
    ap.add_argument("--query-mode", choices=("raw", "rewrite"), default="rewrite",
                    help="raw = apa adanya; rewrite = lewat rag_rewrite.untuk_retrieval().")
    ap.add_argument("--k", default="1,3,5,10", help="Daftar k dipisah koma.")
    ap.add_argument("--patches", default="",
                    help="Modul patch yang diimpor sebelum eval (mis. 'rag_rerank_patch'). Dipisah koma.")
    ap.add_argument("--limit", type=int, default=0, help="Batasi jumlah query (0=semua).")
    ap.add_argument("--out", default="", help="Tulis laporan JSON ke path ini.")
    args = ap.parse_args(argv)

    path = args.goldset
    if not os.path.exists(path):
        alt = "eval_retrieval_goldset.example.jsonl"
        if os.path.exists(alt):
            sys.stderr.write("Gold set %r tak ada; pakai contoh %r.\n" % (path, alt))
            path = alt
        else:
            sys.stderr.write("Gold set tidak ditemukan: %s\n" % path)
            return 2

    if args.patches:
        _apply_patches(args.patches.split(","))

    ks = tuple(int(x) for x in str(args.k).split(",") if x.strip())
    goldset = _load_goldset(path)
    if args.limit:
        goldset = goldset[:args.limit]
    if not goldset:
        sys.stderr.write("Gold set kosong / tak valid.\n")
        return 2

    summary, per_q = evaluate(goldset, ks=ks, query_mode=args.query_mode)
    _print_summary(summary)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "per_query": per_q}, f,
                      ensure_ascii=False, indent=2)
        sys.stderr.write("Laporan ditulis: %s\n" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
