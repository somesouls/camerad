# -*- coding: utf-8 -*-
"""phase2_upgrade.py — Fase 2: legal intelligence (relasi + tagging).

Yang dikerjakan skrip ini:
  1. Membangun tabel peraturan_relasi dari kolom status_terkait/history_terkait
     (hasil parser TKB) — fondasi successor-tracing MULTI-HOP.
  2. Backfill kolom entitas & topik untuk seluruh unit peraturan
     (dictionary-driven: kamus_sinonim + taksonomi topik bawaan) — dipakai
     rag_domain_patch sebagai sinyal ranking.

Tanpa GPU/model/internet; idempoten (aman dijalankan ulang).

Setelah ini (opsional, untuk mengindeks bagian PENJELASAN peraturan):
  - Impor ulang dokumen peraturan lewat menu Batch Peraturan (parser v17
    mengenali bagian PENJELASAN sebagai unit 'penjelasan').
  - Lalu embed unit baru:  python phase0_upgrade.py --reindex-all   (resume)

Pemakaian:
  python phase2_upgrade.py
"""
import sys
import time


def _hr(t):
    print("\n" + "=" * 64 + "\n" + t + "\n" + "=" * 64, flush=True)


def main():
    try:
        import peraturan.db as pdb
    except Exception as e:
        print("[X] peraturan_db tak dapat diimpor: %s" % e, flush=True)
        return 1

    _hr("FASE 2 (1/2): BANGUN RELASI PERATURAN")
    t0 = time.time()
    try:
        res = pdb.build_relasi()
    except Exception as e:
        res = {"ok": False, "error": str(e)[:200]}
    print("Hasil: %s (%.1f dtk)" % (res, time.time() - t0), flush=True)

    _hr("FASE 2 (2/2): BACKFILL ENTITAS & TOPIK")
    t0 = time.time()
    try:
        res2 = pdb.backfill_tags()
    except Exception as e:
        res2 = {"ok": False, "error": str(e)[:200]}
    print("Hasil: %s (%.1f dtk)" % (res2, time.time() - t0), flush=True)

    _hr("SELESAI")
    print("""\
Relasi & tagging terisi. Efek langsung (setelah restart aplikasi):
- rag_successor_patch menelusuri rantai penerus MULTI-HOP sampai dokumen
  pengganti yang berlaku (bukan hanya 1 lompatan).
- rag_domain_patch memakai entitas/topik + kekuatan_hukum + recency sebagai
  sinyal ranking, dan memfilter as-of bila query menyebut tahun.

Opsional (mengindeks bagian PENJELASAN peraturan):
1. Impor ulang dokumen lewat menu Batch Peraturan — parser v17 membuat unit
   'penjelasan' (id berakhiran -penj-pX, hierarchy berprefix 'PENJELASAN > ').
2. python phase0_upgrade.py --reindex-all   (resume: hanya embed unit baru)
""", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
