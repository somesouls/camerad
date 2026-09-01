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

Rantai yang diukur = rantai RANKING produksi (successor -> rerank -> domain).
Gerbang abstain (rag.calibration_patch) sengaja TIDAK dibungkus ke retrieval di
sini: ia keputusan abstain, dilaporkan terpisah pada bagian PROKSI ABSTAIN.

Mode sadar-gerbang (Tahap 4 #1 / A2): jalankan dengan --gate agar eval MENIRU
gerbang cosine produksi (rag.calibration_patch._search_gated) TANPA membungkus
pdb.search — recall tetap terukur apa adanya, lalu dihitung terpisah recall
POST-GATE + false-abstain (kueri 'hit' yang jatuh gara-gara gerbang). Ambang
gerbang di-resolusi PERSIS seperti produksi lewat rag.calibration.get_min_cos()
(knob_store per-profil: store>env>default); override manual via --gate-min-cos.

Track D langkah 2 (cermin rewrite dua-arah): simulasi gerbang & proksi abstain
memakai cosine MAKS lintas-varian kueri (rag.rewrite.varian_kueri -> [asli,
baku]), SAMA seperti rag.calibration_patch produksi, agar metrik post-gate /
false-abstain apel-ke-apel dengan perilaku nyata. Dikendalikan env
RAG_GATE_REWRITE (default on); GAGAL-ANGGUN ke skor query-asli.

Determinisme gerbang: secara DEFAULT eval ini mematikan query-rewrite AI
(RAG_REWRITE_AI=0) supaya skor stabil & --baseline-check apel-ke-apel. Untuk
mengukur perilaku produksi apa adanya, jalankan dengan PHASE4_REWRITE_AI=1.
(Rewrite dua-arah baku bersifat DETERMINISTIK non-LLM, jadi aman untuk gerbang.)

Selain rewrite-AI, reranker GPU (bge-reranker-v2-m3, CUDA/fp16) juga sumber
nondeterminisme: urutan kandidat yang skornya nyaris seri bisa bergeser
antar-run sehingga recall/MRR goyang walau retrieval identik. _setup_determinism()
mengunci seed + algoritma deterministik agar hasil reprodusibel (fail-open;
matikan dgn PHASE4_DETERMINISTIK=0).

Pemakaian:
  python phase4_eval.py --seed                          # isi golden set + cermin ke /rag-eval
  python phase4_eval.py                                 # jalankan evaluasi retrieval
  python phase4_eval.py --k 10                          # recall@10
  python phase4_eval.py --gate                          # mode sadar-gerbang (profil agent)
  python phase4_eval.py --gate --gate-min-cos 0.61      # gerbang eksplisit 0.61
  python phase4_eval.py --gate --gate-profile chatbot   # ambang dari profil chatbot
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

# --- Determinisme gerbang (Tahap 3) -----------------------------------------
# phase4_eval dipakai sebagai GERBANG regresi, jadi skornya harus STABIL agar
# --baseline-check membandingkan apel-ke-apel. Query-rewrite berbasis LLM
# (RAG_REWRITE_AI=1) membuat ranking sedikit non-deterministik (MRR goyang).
# Karena itu DEFAULT eval di sini = rewrite AI OFF (deterministik). Untuk
# mengukur perilaku produksi apa adanya, jalankan dengan PHASE4_REWRITE_AI=1.
# CATATAN: RAG_REWRITE_AI dibaca rag.rerank_patch saat IMPOR, jadi env ini
# WAJIB di-set sebelum blok impor patch di bawah.
if os.environ.get("PHASE4_REWRITE_AI", "0").strip().lower() in ("1", "true", "yes"):
    os.environ.setdefault("RAG_REWRITE_AI", "1")
else:
    os.environ["RAG_REWRITE_AI"] = "0"
_REWRITE_AI = os.environ.get("RAG_REWRITE_AI", "0")
print("[phase4_eval] rewrite_ai=%s (0=deterministik utk gerbang; "
      "set PHASE4_REWRITE_AI=1 utk mode produksi)" % _REWRITE_AI, flush=True)

# CUBLAS_WORKSPACE_CONFIG WAJIB di-set SEBELUM konteks CUDA/cuBLAS dibuat agar
# torch.use_deterministic_algorithms(True) tidak menolak operasi cuBLAS. Set di
# sini (sebelum blok impor patch yang menarik torch) supaya efektif. Fail-open
# utk CPU / non-CUDA (variabel ini diabaikan). Determinisme penuh diaktifkan
# oleh _setup_determinism() saat run_eval(); matikan semua dgn PHASE4_DETERMINISTIK=0.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

# Impor patch RANKING retrieval (successor -> rerank -> domain), urutan sama
# seperti web_app.py. rag.calibration_patch (gerbang abstain cosine >=
# RAG_MIN_COS) SENGAJA tidak diimpor: gerbang itu keputusan abstain, bukan
# retrieval — bila dibungkus ke pdb.search ia membuang baris < ambang sehingga
# recall@k salah ukur dan proksi abstain kehilangan skor. Perilaku gerbang
# dilaporkan terpisah di bagian PROKSI ABSTAIN (via rag.calibration).
for _m in ("rag.successor_patch", "rag.rerank_patch", "rag.domain_patch"):
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


# --- Rewrite dua-arah untuk gerbang (Track D langkah 2, cermin produksi) ------
# Cermin dari rag.calibration_patch: simulasi gerbang & proksi abstain menilai
# cosine MAKS lintas-varian kueri (varian_kueri -> [asli, baku]) agar metrik
# post-gate/false-abstain apel-ke-apel dengan gerbang produksi. Env
# RAG_GATE_REWRITE (default on); GAGAL-ANGGUN ke [asli] bila modul tak ada.
def _gate_rewrite_enabled():
    v = os.environ.get("RAG_GATE_REWRITE", "1")
    return str(v).strip().lower() not in ("0", "false", "no")


def _query_variants(query):
    if not _gate_rewrite_enabled():
        return [query]
    try:
        import rag.rewrite as _rw
        vs = _rw.varian_kueri(query)
        return vs if vs else [query]
    except Exception:
        return [query]


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


def _resolve_gate(gate_profile=None, explicit=None):
    """Ambang gerbang efektif untuk mode sadar-gerbang (Tahap 4 #1 / A2).

    Precedence PERSIS seperti produksi via rag.calibration.get_min_cos():
    --gate-min-cos eksplisit > knob_store per-profil (store>env>default) >
    env RAG_MIN_COS. Kembalikan None / <= 0 sebagai 'gerbang mati'. GAGAL-ANGGUN:
    bila rag.calibration/knob_store tak tersedia, jatuh ke env RAG_MIN_COS.
    """
    if explicit is not None:
        try:
            mc = float(explicit)
        except Exception:
            return None
        return mc if mc > 0 else None
    if _cal is not None:
        try:
            _cal.set_profile((gate_profile or "").strip() or None)
            try:
                mc = float(_cal.get_min_cos())
            finally:
                _cal.reset_profile()
            return mc if mc > 0 else None
        except Exception:
            pass
    return _gate_min_cos()


def _cos_map(query, rows):
    """{id: cosine MAKS lintas-varian kueri} untuk baris hasil retrieval.

    Cermin rag.calibration_patch._skor_maks_varian: nilai cosine tertinggi atas
    {query asli, varian baku} (rag.rewrite.varian_kueri). Fail-open kosong.
    """
    if not rows or _cal is None:
        return {}
    try:
        ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
        if not ids:
            return {}
        best = {}
        for v in _query_variants(query):
            try:
                skor = _cal.skor_peraturan(v, ids) or {}
            except Exception:
                skor = {}
            for i, c in skor.items():
                if c is None:
                    continue
                if i not in best or c > best[i]:
                    best[i] = c
        return best
    except Exception:
        return {}


def _apply_gate(rows, cos, mc):
    """Tiru rag.calibration_patch._search_gated: buang baris dgn cosine < mc;
    baris tanpa skor cosine di-fail-open (dipertahankan). mc None/0 -> apa adanya.
    """
    if not mc:
        return list(rows or [])
    keep = []
    for d in (rows or []):
        if not isinstance(d, dict):
            keep.append(d)
            continue
        c = cos.get(d.get("id"))
        if c is None or c >= mc:
            keep.append(d)
    return keep


def _setup_determinism(seed=1234):
    """Kunci semua sumber acak + paksa algoritma deterministik agar skor eval
    STABIL antar-run (--baseline-check apel-ke-apel).

    Nondeterminisme reranker GPU (bge-reranker-v2-m3, CUDA/fp16) dapat menggeser
    urutan kandidat yang skornya nyaris seri (mis. klaster IKN / kawasan berikat)
    sehingga recall@k & MRR goyang antar-run walau RETRIEVAL identik. Ini pernah
    memicu [GAGAL] palsu pada gerbang baseline. Fungsi ini menyetel seed +
    cudnn.deterministic + use_deterministic_algorithms(warn_only) agar hasil
    reprodusibel. FAIL-OPEN: bila torch/CUDA tak mendukung mode deterministik,
    eval tetap jalan (hanya kembali sedikit goyang). Nonaktifkan dengan
    PHASE4_DETERMINISTIK=0.
    """
    if os.environ.get("PHASE4_DETERMINISTIK", "1").strip().lower() in ("0", "false", "no"):
        print("[phase4_eval] determinisme dimatikan (PHASE4_DETERMINISTIK=0).", flush=True)
        return
    try:
        import random as _random
        _random.seed(seed)
    except Exception:
        pass
    try:
        import numpy as _np
        _np.random.seed(seed)
    except Exception:
        pass
    try:
        import torch as _torch
    except Exception as _e:
        print("[phase4_eval] torch tak tersedia; determinisme dilewati: %s" % _e, flush=True)
        return
    try:
        _torch.manual_seed(seed)
        if _torch.cuda.is_available():
            _torch.cuda.manual_seed_all(seed)
    except Exception as _e:
        print("[phase4_eval] set seed torch gagal: %s" % _e, flush=True)
    try:
        _torch.backends.cudnn.deterministic = True
        _torch.backends.cudnn.benchmark = False
    except Exception:
        pass
    try:
        # warn_only=True: bila ada operasi tanpa implementasi deterministik,
        # JANGAN meledak — cukup peringatan; eval tetap jalan.
        _torch.use_deterministic_algorithms(True, warn_only=True)
        print("[phase4_eval] determinisme AKTIF (seed=%d, cudnn.deterministic=on, "
              "use_deterministic_algorithms=warn_only)." % seed, flush=True)
    except Exception as _e:
        print("[phase4_eval] use_deterministic_algorithms gagal (fail-open): %s"
              % _e, flush=True)


def run_eval(k=10, limit=None, quiet=False, gate=False, gate_min_cos=None,
             gate_profile="agent"):
    _setup_determinism()
    entries = gdb.list_golden(only_aktif=True)
    if limit:
        entries = entries[:int(limit)]
    hits = [e for e in entries if e.get("jenis_harapan") == "hit"]
    abst = [e for e in entries if e.get("jenis_harapan") == "abstain"]
    if not entries:
        print("Golden set kosong. Jalankan dulu: python phase4_eval.py --seed")
        return None

    # Mode sadar-gerbang (Tahap 4 #1 / A2): resolusi ambang PERSIS seperti
    # produksi (sweep > knob_store per-profil > env > default). Gerbang TIDAK
    # dibungkus ke pdb.search; kita hitung cosine hasil lalu tiru penyaringan
    # rag.calibration_patch._search_gated untuk mengukur recall POST-GATE +
    # false-abstain (kueri 'hit' yang jatuh gara-gara gerbang).
    gate_mc = _resolve_gate(gate_profile, gate_min_cos) if gate else None
    if gate:
        if gate_mc is None:
            print("\n[phase4_eval] mode --gate diminta tetapi ambang gerbang "
                  "<= 0 / tak teresolusi (profil=%s). Metrik post-gate dilewati."
                  % gate_profile, flush=True)
        else:
            print("\n[phase4_eval] MODE SADAR-GERBANG aktif: min_cos=%.3f "
                  "(profil=%s%s | rewrite-gerbang=%s)."
                  % (gate_mc, gate_profile,
                     ", eksplisit" if gate_min_cos is not None else "",
                     "on" if _gate_rewrite_enabled() else "off"),
                  flush=True)

    print("\n== EVALUASI HIT (recall@%d) ==" % k, flush=True)
    per_q, n_hit, rr_sum = [], 0, 0.0
    n_hit_gated, n_false_abstain, false_abstain_q = 0, 0, []
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
        rec = {"query": q, "hit": ok, "rank": rank, "top1": _top1_label(rows)}
        if gate and gate_mc is not None:
            cos = _cos_map(q, rows)
            gated = _apply_gate(rows, cos, gate_mc)
            grank = _match_rank(gated, e.get("expect") or {})
            gok = bool(grank and grank <= k)
            vals = [float(v) for v in cos.values() if v is not None]
            rec["gated_rank"] = grank
            rec["gated_hit"] = gok
            rec["max_cos"] = round(max(vals), 4) if vals else None
            if gok:
                n_hit_gated += 1
            if ok and not gok:
                n_false_abstain += 1
                false_abstain_q.append(q)
        per_q.append(rec)
        if not quiet:
            extra = ""
            if gate and gate_mc is not None:
                extra = "  | post-gate=%s" % ("HIT" if rec.get("gated_hit") else "ABSTAIN")
            print("  [%s] rank=%-2d | %s%s%s"
                  % ("HIT " if ok else "MISS", rank, q,
                     ("  <- top1: " + _top1_label(rows)) if not ok and rows else "",
                     extra),
                  flush=True)

    recall = (n_hit / len(hits)) if hits else 0.0
    mrr = (rr_sum / len(hits)) if hits else 0.0
    print("  -> recall@%d = %.3f (%d/%d) | MRR = %.3f"
          % (k, recall, n_hit, len(hits), mrr), flush=True)

    post_gate_recall = None
    false_abstain_rate = None
    if gate and gate_mc is not None and hits:
        post_gate_recall = n_hit_gated / len(hits)
        false_abstain_rate = n_false_abstain / len(hits)
        print("  -> [sadar-gerbang] recall POST-GATE@%d = %.3f (%d/%d) | "
              "false-abstain = %.3f (%d/%d) @min_cos=%.3f"
              % (k, post_gate_recall, n_hit_gated, len(hits),
                 false_abstain_rate, n_false_abstain, len(hits), gate_mc),
              flush=True)
        if false_abstain_q and not quiet:
            print("     false-abstain (hit ungated -> abstain post-gate):", flush=True)
            for _fq in false_abstain_q:
                print("       - %s" % _fq, flush=True)

    print("\n== PROKSI ABSTAIN (bukan keputusan LLM) ==", flush=True)
    mc_gate = gate_mc if (gate and gate_mc is not None) else _gate_min_cos()
    abs_rows = []
    n_abstain_leak = 0
    for e in abst:
        q = e["query"]
        try:
            rows = pdb.search(q, k=5)
        except Exception:
            rows = []
        max_cos = None
        if rows and _cal is not None:
            try:
                skor = _cos_map(q, rows)
                vals = [float(v) for v in skor.values() if v is not None]
                if vals:
                    max_cos = max(vals)
            except Exception:
                max_cos = None
        risk = bool(mc_gate and max_cos is not None and max_cos >= mc_gate)
        if risk:
            n_abstain_leak += 1
        abs_rows.append({"query": q, "n_hasil": len(rows),
                         "max_cos": (round(max_cos, 4) if max_cos is not None else None),
                         "berisiko_lolos_gerbang": risk})
        if not quiet:
            print("  [abstain] max_cos=%s | %s"
                  % (("%.3f" % max_cos) if max_cos is not None else "-", q), flush=True)
    if mc_gate is None:
        print("  (gerbang RAG_MIN_COS belum aktif — kolom risiko tidak dinilai)",
              flush=True)
    elif gate:
        n_ab = len(abst)
        print("  -> [sadar-gerbang] abstain benar = %d/%d | bocor (lolos gerbang) "
              "= %d/%d @min_cos=%.3f"
              % (n_ab - n_abstain_leak, n_ab, n_abstain_leak, n_ab, mc_gate),
              flush=True)

    return {
        "ts": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "k": int(k),
        "n_hit_queries": len(hits),
        "n_abstain_queries": len(abst),
        "recall_at_k": round(recall, 4),
        "mrr": round(mrr, 4),
        "min_cos_gate": mc_gate,
        "gate_mode": bool(gate and gate_mc is not None),
        "gate_min_cos": (round(gate_mc, 4) if gate_mc is not None else None),
        "gate_profile": (gate_profile if gate else None),
        "gate_rewrite": (_gate_rewrite_enabled() if (gate and gate_mc is not None) else None),
        "post_gate_recall_at_k": (round(post_gate_recall, 4)
                                  if post_gate_recall is not None else None),
        "n_false_abstain": (n_false_abstain if (gate and gate_mc is not None) else None),
        "false_abstain_rate": (round(false_abstain_rate, 4)
                               if false_abstain_rate is not None else None),
        "false_abstain_queries": (false_abstain_q
                                  if (gate and gate_mc is not None) else None),
        "n_abstain_leak": (n_abstain_leak if (gate and gate_mc is not None) else None),
        "rewrite_ai": _REWRITE_AI,
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
               "recall_at_k": rep["recall_at_k"], "mrr": rep["mrr"],
               "rewrite_ai": rep.get("rewrite_ai")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ringkas, f, ensure_ascii=False, indent=2)
    print("Baseline tersimpan ke %s : recall@%d=%.3f mrr=%.3f (n=%d) rewrite_ai=%s"
          % (path, rep["k"], rep["recall_at_k"], rep["mrr"], rep["n_hit_queries"],
             rep.get("rewrite_ai")),
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
    if base.get("rewrite_ai") is not None and str(base.get("rewrite_ai")) != str(rep.get("rewrite_ai")):
        print("  [!] MODE BEDA: baseline rewrite_ai=%s vs run=%s — skor tak apel-ke-apel; "
              "re-baseline pada mode sama (default eval kini deterministik)."
              % (base.get("rewrite_ai"), rep.get("rewrite_ai")))
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
    ap.add_argument("--gate", action="store_true",
                    help="mode sadar-gerbang: ukur recall POST-GATE + false-abstain "
                         "(ambang di-resolusi knob_store per-profil, spt produksi)")
    ap.add_argument("--gate-min-cos", type=float, default=None,
                    help="override ambang gerbang (mis. 0.61); default = resolusi "
                         "knob_store profil --gate-profile")
    ap.add_argument("--gate-profile", default="agent",
                    help="profil sumber ambang gerbang (agent|chatbot); default agent")
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

    rep = run_eval(k=args.k, limit=args.limit, quiet=args.quiet,
                   gate=args.gate, gate_min_cos=args.gate_min_cos,
                   gate_profile=args.gate_profile)
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
