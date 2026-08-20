# -*- coding: utf-8 -*-
"""phase5_qa_build.py — Bangun indeks Q&A historis (Fase 5).

Mengumpulkan pasangan pertanyaan-jawaban dari:
  * Sosmed (FAQ terjawab: balasan akun resmi), dan
  * Livechat AWE (giliran pelanggan -> pertanyaan, petugas manusia -> jawaban;
    Bot/CCAI & full-bot dibuang),

lalu: mask PII -> dedup -> deteksi+resolusi rujukan peraturan di jawaban
(regref) -> embed PERTANYAAN dengan model bge-m3 (resume per dimensi model).

Pemakaian:
  python phase5_qa_build.py                 # bangun/isi (idempoten + resume)
  python phase5_qa_build.py --stats         # lihat ringkasan indeks
  python phase5_qa_build.py --limit-awe 3000 --limit-sosmed 3000
  python phase5_qa_build.py --batch 96      # percepat embed di GPU

Jalankan ulang kapan saja (mis. mingguan) — hanya pasangan baru yang di-embed.
"""
import argparse
import sys
import time


def main():
    ap = argparse.ArgumentParser(description="Bangun indeks Q&A historis (Fase 5)")
    ap.add_argument("--stats", action="store_true", help="ringkasan indeks saja")
    ap.add_argument("--limit-sosmed", type=int, default=2000)
    ap.add_argument("--limit-awe", type=int, default=1500)
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    import db.qa_index_db as qa

    if args.stats:
        print("Statistik indeks Q&A:", qa.stats())
        return 0

    print("=" * 64)
    print(" FASE 5 — BANGUN INDEKS Q&A HISTORIS (Q2Q)")
    print("=" * 64, flush=True)
    t0 = time.time()
    res = qa.build_index(batch=args.batch, limit_sosmed=args.limit_sosmed,
                         limit_awe=args.limit_awe)
    dt = time.time() - t0
    print("\nHasil build:", res, "(%.1f dtk)" % dt, flush=True)
    print("Statistik   :", qa.stats(), flush=True)
    if res.get("ok"):
        print("""\

Indeks Q&A siap. Mulai restart berikutnya, jalur AWE/Sosmed otomatis menambah
hasil Q2Q + tautan peraturan terverifikasi (patch rag_qa_patch).
Uji di /rag-lab dengan pertanyaan informal yang mirip riwayat pengguna, lalu
pastikan jawaban mengutip pasal hasil tautan otomatis.
""", flush=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
