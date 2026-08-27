# -*- coding: utf-8 -*-
"""phase4_eval.py — Evaluasi retrieval atas GOLDEN SET (Fase 4), tanpa LLM.

Mengukur kualitas RETRIEVAL (bukan jawaban) secara deterministik:

  * recall@k : fraksi query 'hit' yang rujukan harapannya muncul di top-k.
  * MRR      : Mean Reciprocal Rank posisi rujukan harapan.
  * Proksi abstain: untuk query 'abstain', dilaporkan cosine teratas hasil
    retrieval (via rag.calibration.skor_peraturan) + penanda 'berisiko lolos
    gerbang' bila >= RAG_MIN_COS aktif. (Penilaian abstain END-TO-END tetap
    lewat /rag-eval jenis=golden — skrip ini sengaja tidak memanggil LLM agar
    murah & bisa jadi gerbang cepat.)

Rantai yang diukur = rantai produksi: patch diimpor dengan urutan yang sama
seperti web_app.py (successor -> rerank -> kalibrasi -> domain).

Pemakaian:
  python phase4_eval.py --seed                          # isi golden set + cermin ke /rag-eval
  python phase4_eval.py                                 # jalankan evaluasi retrieval
  python phase4_eval.py --k 10                          # recall@10
  python phase4_eval.py --baseline-save golden_base.json
  python phase4_eval.py --baseline-check golden_base.json [--tolerance 0.05]
  python phase4_eval.py --mine                          # kandidat golden dari feedback produksi

Gerbang upgrade: simpan baseline SESUDAH perubahan tervalidasi; sebelum upgrade
berikutnya jalankan --baseline-check — exit code 1 bila recall/MRR turun
melebihi toleransi.
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys

# Muat .env (bila ada) agar env RAG_* / model ikut.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Impor patch retrieval dengan URUTAN sama seperti web_app.py agar yang diukur
# adalah rantai produksi (successor -> rerank -> kalibrasi -> domain).
for _m in ("rag.successor_patch", "rag.rerank_patch",
           "rag.calibration_patch", "rag.domain_patch"):
    try:
        __import__(_m)
    except Exception as _e:  # fail-soft: lanjut tanpa patch tsb
        print("[phase4_eval] impor %s dilewati: %s" % (_m, _e), flush=True)

import peraturan.db as pdb
import rag.golden_db as gdb

try:
    import rag.calibration as _cal
except Exception:            # pragma: no cover
    _cal = None


def _utcnow():
    return _dt.datetime.now(_dt.timezone.utc)


# ------------------------------------------------------------------ matching
def _norm_key(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _row_text(d):
    return " ".join(str(d.get(k) or "")
                    for k in ("judul", "hierarchy", "isi")).lower()


def _match_rank(rows, expect):
    """Rank 1-based baris pertama yang cocok ekspektasi; 0 bila tak ada.

    Cocok bila: salah satu nomor harapan cocok (substring dua arah setelah
    dinormalisasi) ATAU semua keywords muncul pada SATU baris.
    """
    nomors = [_norm_key(x) for x in (expect.get("nomor") or []) if str(x).strip()]
    nomors = [n for n in nomors if n]
    kws = [str(x).lower().strip() for x in (expect.get("keywords") or []) if str(x).strip()]
    for i, r in enumerate(rows, start=1):
        rn = _norm_key(r.get("nomor"))
        hit_nomor = False
        if rn:
            for en in nomors:
                if en in rn or rn in en:
                    hit_nomor = True
                    break
        txt = _row_text(r)
        hit_kw = bool(kws) and all(kw in txt for kw in kws)
        if hit_nomor or hit_kw:
            return i
    return 0


def _top1_label(rows):
    if not rows:
        return ""
    r0 = rows[0]
    parts = [str(r0.get("jenis_peraturan") or ""), str(r0.get("nomor") or "")]
    lab = " ".join(p for p in parts if p).strip()
    if r0.get("pasal"):
        lab += " - Pasal %s" % r0["pasal"]
    return lab


# -------------------------------------------------------------------- evaluasi
def _gate_min_cos():
    try:
        mc = float(os.environ.get("RAG_MIN_COS", "0") or 0)
        return mc if mc > 0 else None
    except Exception:
        return None


def run_eval(k=10, limit=None, quiet=False):
    entries = gdb.list_golden(only_aktif=True)
    if limit:
        entries = entries[:int(limit)]
    hits = [e for e in entries if e.get("jenis_harapan") == "hit"]
    abst = [e for e in entries if e.get("jenis_harapan") == "abstain"]
    if not entries:
        print("Golden set kosong. Jalankan dulu: python phase4_eval.py --seed")
        return None

    print("\n== EVALUASI HIT (recall@%d) ==" % k, flush=True)
    per_q, n_hit, rr_sum = [], 0, 0.0
    for e in hits:
        q = e["query"]
        try:
            rows = pdb.search(q, k=k)
        except Exception as ex:
            rows = []
            print("  [X] search gagal utk '%s': %s" % (q, str(ex)[:120]), flush=True)
        rank = _match_rank(rows, e.get("expect") or {})
        ok = bool(rank and rank <= k)
        n_hit += 1 if ok else 0
        rr_sum += (1.0 / rank) if ok else 0.0
        per_q.append({"query": q, "hit": ok, "rank": rank, "top1": _top1_label(rows)})
        if not quiet:
            print("  [%s] rank=%-2d | %s%s"
                  % ("HIT " if ok else "MISS", rank, q,
                     ("  <- top1: " + _top1_label(rows)) if not ok and rows else ""),
                  flush=True)

    recall = (n_hit / len(hits)) if hits else 0.0
    mrr = (rr_sum / len(hits)) if hits else 0.0
    print("  -> recall@%d = %.3f (%d/%d) | MRR = %.3f"
          % (k, recall, n_hit, len(hits), mrr), flush=True)

    print("\n== PROKSI ABSTAIN (bukan keputusan LLM) ==", flush=True)
    mc_gate = _gate_min_cos()
    abs_rows = []
    for e in abst:
        q = e["query"]
        try:
            rows = pdb.search(q, k=5)
        except Exception:
            rows = []
        max_cos = None
        if rows and _cal is not None:
            try:
                ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
                skor = _cal.skor_peraturan(q, ids) or {}
                vals = [float(v) for v in skor.values() if v is not None]
                if vals:
                    max_cos = max(vals)
            except Exception:
                max_cos = None
        risk = bool(mc_gate and max_cos is not None and max_cos >= mc_gate)
        abs_rows.append({"query": q, "n_hasil": len(rows),
                         "max_cos": (round(max_cos, 4) if max_cos is not None else None),
                         "berisiko_lolos_gerbang": risk})
        if not quiet:
            print("  [abstain] max_cos=%s | %s"
                  % (("%.3f" % max_cos) if max_cos is not None else "-", q), flush=True)
    if mc_gate is None:
        print("  (gerbang RAG_MIN_COS belum aktif — kolom risiko tidak dinilai)",
              flush=True)

    return {
        "ts": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "k": int(k),
        "n_hit_queries": len(hits),
        "n_abstain_queries": len(abst),
        "recall_at_k": round(recall, 4),
        "mrr": round(mrr, 4),
        "min_cos_gate": mc_gate,
        "per_query": per_q,
        "abstain": abs_rows,
    }


def _save_report(rep):
    outdir = os.environ.get("PIPELINE_RUNS_DIR") or "_runs"
    try:
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, "golden_eval_%s.json"
                            % _utcnow().strftime("%Y%m%d_%H%M%S"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print("\nLaporan tersimpan: %s" % path, flush=True)
    except Exception as e:
        print("\n[!] gagal menyimpan laporan: %s" % e, flush=True)


# ------------------------------------------------------------------ baseline
def baseline_save(rep, path):
    ringkas = {"ts": rep["ts"], "k": rep["k"], "n_hit_queries": rep["n_hit_queries"],
               "recall_at_k": rep["recall_at_k"], "mrr": rep["mrr"]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ringkas, f, ensure_ascii=False, indent=2)
    print("Baseline tersimpan ke %s : recall@%d=%.3f mrr=%.3f (n=%d)"
          % (path, rep["k"], rep["recall_at_k"], rep["mrr"], rep["n_hit_queries"]),
          flush=True)


def baseline_check(rep, path, tolerance=0.05):
    try:
        with open(path, "r", encoding="utf-8") as f:
            base = json.load(f)
    except Exception as e:
        print("[X] baseline tak terbaca (%s): %s" % (path, e))
        return 2
    d_recall = rep["recall_at_k"] - float(base.get("recall_at_k") or 0.0)
    d_mrr = rep["mrr"] - float(base.get("mrr") or 0.0)
    print("\n== GERBANG BASELINE ==")
    print("  recall: %.3f vs baseline %.3f (delta %+.3f)"
          % (rep["recall_at_k"], base.get("recall_at_k") or 0.0, d_recall))
    print("  mrr   : %.3f vs baseline %.3f (delta %+.3f)"
          % (rep["mrr"], base.get("mrr") or 0.0, d_mrr))
    if d_recall < -tolerance or d_mrr < -tolerance:
        print("  [GAGAL] regresi melebihi toleransi %.2f — JANGAN lanjutkan upgrade."
              % tolerance)
        return 1
    print("  [OK] tidak ada regresi di luar toleransi %.2f." % tolerance)
    return 0


# ----------------------------------------------------------------------- mine
def do_mine():
    print("\n== KANDIDAT GOLDEN SET DARI FEEDBACK PRODUKSI ==")
    res = gdb.mine_feedback()
    if not res.get("ok"):
        print("  [X] %s" % res.get("error"))
        return
    items = res.get("items") or []
    if not items:
        print("  (belum ada jempol-down / fallback tercatat)")
        return
    for it in items[:20]:
        print("  down=%-2d fallback=%-2d | %s"
              % (it["n_down"], it["n_fallback"], it["question"][:90]))
    print("""\

Kurasi manual: untuk pertanyaan yang SEHARUSNYA terjawab, tambahkan ke golden
set (contoh):
  python -c "import rag.golden_db as g; g.upsert_golden('<pertanyaan>', 'hit',
        {'keywords': ['<kata kunci wajib>']}, catatan='dari feedback')"
Untuk pertanyaan yang SEHARUSNYA abstain, pakai jenis_harapan='abstain'.""")


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Evaluasi retrieval golden set (Fase 4)")
    ap.add_argument("--seed", action="store_true",
                    help="isi golden set bawaan + cermin ke eval_sample")
    ap.add_argument("--mine", action="store_true",
                    help="tambang kandidat golden dari feedback produksi")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--baseline-save", metavar="FILE", default=None)
    ap.add_argument("--baseline-check", metavar="FILE", default=None)
    ap.add_argument("--tolerance", type=float, default=0.05)
    args = ap.parse_args()

    if args.seed:
        n = gdb.seed_default()
        print("Seed golden set: +%d entri bawaan." % n)
        try:
            fx = gdb.fix_seed_v2()
            if fx.get("updated"):
                print("Ekspektasi dilonggarkan (v20): %d entri." % fx["updated"])
        except Exception:
            pass
        m = gdb.mirror_to_eval()
        print("Cermin ke /rag-eval (eval_sample jenis=golden): %s" % m)
        if not any([args.mine, args.baseline_save, args.baseline_check]):
            return 0

    if args.mine:
        do_mine()
        if not any([args.baseline_save, args.baseline_check]):
            return 0

    rep = run_eval(k=args.k, limit=args.limit, quiet=args.quiet)
    if rep is None:
        return 2
    _save_report(rep)
    if args.baseline_save:
        baseline_save(rep, args.baseline_save)
    if args.baseline_check:
        return baseline_check(rep, args.baseline_check, tolerance=args.tolerance)
    return 0


if __name__ == "__main__":
    sys.exit(main())
