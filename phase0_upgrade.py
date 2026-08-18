# -*- coding: utf-8 -*-
"""phase0_upgrade.py — Eksekutor Fase 0 upgrade RAG camerad.

Dijalankan di MESIN LOKAL tempat camerad berjalan:

  1. Cek ketersediaan & perangkat model embedding (peraturan_semantic) dan
     reranker (rag_reranker). Pemuatan pertama kali akan MENGUNDUH model
     (bge-m3 ~2,2 GB; bge-reranker-v2-m3 ~1,1 GB) — butuh koneksi internet.
  2. Deteksi mismatch dimensi vektor tersimpan vs model aktif (terjadi setelah
     ganti model, mis. e5-base 768-d -> bge-m3 1024-d).
  3. Reindex embedding peraturan & SOP bila diminta/diperlukan. Reindex bersifat
     RESUME: unit yang sudah berdimensi model aktif dilewati, jadi Ctrl+C /
     interupsi TIDAK kehilangan progres — jalankan ulang untuk melanjutkan.
  4. Tampilkan panduan langkah berikutnya (kalibrasi ambang via /rag-eval).

Pemakaian:
  python phase0_upgrade.py                  # cek saja (tanpa reindex)
  python phase0_upgrade.py --reindex-all    # reindex peraturan + SOP (resume)
  python phase0_upgrade.py --force          # reindex PENUH (embed ulang semua)
  python phase0_upgrade.py --peraturan-only / --sop-only

Tips kecepatan (GPU): naikkan batch encode, mis. PowerShell:
  $env:PERATURAN_EMBED_BATCH=96 ; python phase0_upgrade.py --reindex-all
"""
import argparse
import sys
import time


def _hr(t):
    print("\n" + "=" * 64 + "\n" + t + "\n" + "=" * 64, flush=True)


def cek_model_embedding():
    try:
        import peraturan_semantic as psem
    except Exception as e:
        print("[X] peraturan_semantic tak dapat diimpor: %s" % e, flush=True)
        return None
    print("Model embedding : %s" % psem.model_id(), flush=True)
    print("Prefix query    : %r | prefix passage: %r"
          % (psem.query_prefix(), psem.passage_prefix()), flush=True)
    print("(Pemuatan pertama akan mengunduh model — mohon tunggu...)", flush=True)
    try:
        avail = psem.is_available()
    except Exception as e:
        print("[X] gagal memuat model: %s" % e, flush=True)
        return None
    print("Ketersediaan    : %s" % ("OK" if avail else "TIDAK TERSEDIA"), flush=True)
    if avail:
        print("Dimensi model   : %d" % psem.embed_dim(), flush=True)
    return psem if avail else None


def cek_reranker():
    try:
        import rag_reranker as rr
    except Exception as e:
        print("[X] rag_reranker tak dapat diimpor: %s" % e, flush=True)
        return None
    info = rr.device_info()
    print("Model reranker  : %s" % info.get("model"), flush=True)
    print("Perangkat       : %s (torch=%s cuda_build=%s)"
          % (info.get("device"), info.get("torch"), info.get("cuda_build")), flush=True)
    try:
        ok = rr.is_available()
    except Exception:
        ok = False
    print("Ketersediaan    : %s"
          % ("OK" if ok else "TIDAK TERSEDIA (rerank dilewati, fail-soft)"), flush=True)
    return rr if ok else None


def _dim_tersimpan(mod, tabel_vec):
    """{dim: jumlah} vektor yang tersimpan pada tabel vektor sebuah modul DB."""
    try:
        conn = mod.init_db(mod.connect())
        try:
            rows = conn.execute(
                "SELECT dim, COUNT(*) AS n FROM %s GROUP BY dim" % tabel_vec
            ).fetchall()
            return {int(r["dim"]): int(r["n"]) for r in rows}
        finally:
            conn.close()
    except Exception as e:
        print("[!] gagal baca %s: %s" % (tabel_vec, e), flush=True)
        return {}


def reindex_satu(nama, mod, tabel_vec, dim_model, force=False):
    _hr("REINDEX: %s" % nama)
    dims = _dim_tersimpan(mod, tabel_vec)
    print("Vektor tersimpan: %s" % (dims or "(kosong)"), flush=True)
    mismatch = bool(dims) and bool(dim_model) and any(d != dim_model for d in dims)
    if not dims:
        print("Belum ada vektor -> reindex penuh (resume).", flush=True)
    elif not dim_model:
        print("Dimensi model tak diketahui -> reindex penuh (amankan).", flush=True)
    elif mismatch:
        print("MISMATCH dimensi (tersimpan %s vs model %d) -> reindex RESUME "
              "(unit yang sudah %d-d dilewati; Ctrl+C aman, jalankan ulang "
              "untuk melanjutkan)." % (sorted(dims), dim_model, dim_model),
              flush=True)
    elif force:
        print("Dimensi cocok, tapi --force diminta -> reindex PENUH.", flush=True)
    else:
        print("Dimensi cocok (%d). Lewati (pakai --force bila ingin tetap reindex)."
              % dim_model, flush=True)
        return
    t0 = time.time()
    try:
        res = mod.reindex(resume=(not force))
    except TypeError:
        res = mod.reindex()  # kompatibilitas modul lama tanpa argumen resume
    except Exception as e:
        print("[X] reindex gagal: %s" % e, flush=True)
        return
    dt = time.time() - t0
    if isinstance(res, dict) and res.get("ok"):
        n = res.get("n", 0)
        extra = ""
        if res.get("skipped"):
            extra = " (lewati %d dari %d unit yang sudah sesuai)" % (
                res.get("skipped"), res.get("total", 0))
        print("[OK] %d unit di-embed dalam %.1f dtk (%.2f unit/dtk)%s."
              % (n, dt, (n / dt) if dt > 0 else 0, extra), flush=True)
    else:
        print("[X] reindex bermasalah: %s" % res, flush=True)


def panduan_berikutnya():
    _hr("LANGKAH BERIKUTNYA (dijalankan admin)")
    print("""\
1. Restart aplikasi (web_app.py) agar model & kamus baru termuat.
2. Kalibrasi ambang cosine:
   a. Buka menu /rag-eval.
   b. Jalankan sweep pada golden set (sweep menguji beberapa nilai RAG_MIN_COS).
   c. Pilih ambang dengan keseimbangan abstain/akurasi terbaik, lalu set di .env:
        RAG_MIN_COS=<nilai_terpilih>
   d. Restart aplikasi lagi.
3. Uji cepat di /rag-lab dengan query informal, mis.:
   - "peraturan yang mengatur SPLN"
   - "ketentuan penyerahan BKP dari luar daerah pabean ke kawasan berikat"
   Pastikan hit Peraturan relevan dan jawaban mencantumkan pasal yang tepat.
4. Pantau log startup: pastikan baris [peraturan_semantic] & [rag_reranker]
   menunjukkan device=cuda bila GPU tersedia. Bila VRAM terbatas, set
   PERATURAN_EMBED_DEVICE=cpu dan/atau RAG_RERANK_DEVICE=cpu.
5. Tips kecepatan reindex di GPU: naikkan batch encode sebelum menjalankan
   skrip, mis. PowerShell:  $env:PERATURAN_EMBED_BATCH=96
6. Reindex bersifat RESUME: Ctrl+C / interupsi TIDAK kehilangan progres —
   jalankan ulang perintah yang sama untuk melanjutkan sisa unit.
""", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Eksekutor Fase 0 upgrade RAG camerad")
    ap.add_argument("--reindex-all", action="store_true",
                    help="reindex peraturan & SOP (resume: hanya unit yang perlu)")
    ap.add_argument("--force", action="store_true",
                    help="paksa reindex PENUH (embed ulang semua unit)")
    ap.add_argument("--peraturan-only", action="store_true")
    ap.add_argument("--sop-only", action="store_true")
    args = ap.parse_args()

    _hr("CEK MODEL EMBEDDING")
    psem = cek_model_embedding()
    _hr("CEK RERANKER")
    cek_reranker()

    if psem is None:
        print("\n[!] Model embedding tidak tersedia. Pastikan dependensi terpasang "
              "(torch + sentence-transformers) dan koneksi internet untuk unduhan "
              "model pertama kali. Reindex dilewati.", flush=True)
        panduan_berikutnya()
        return 1

    dim_model = psem.embed_dim()
    do_reindex = args.reindex_all or args.force
    if not do_reindex:
        _hr("CEK DIMENSI VEKTOR TERSIMPAN (tanpa reindex)")
    targets = []
    if not args.sop_only:
        targets.append(("PERATURAN", "peraturan_db", "peraturan_vec"))
    if not args.peraturan_only:
        targets.append(("SOP", "sop_db", "sop_vec"))
    for nama, mod_name, tabel in targets:
        try:
            mod = __import__(mod_name)
        except Exception as e:
            print("[X] %s tak dapat diimpor: %s" % (mod_name, e), flush=True)
            continue
        if do_reindex:
            reindex_satu(nama, mod, tabel, dim_model, force=args.force)
        else:
            dims = _dim_tersimpan(mod, tabel)
            ok = bool(dims) and bool(dim_model) and all(d == dim_model for d in dims)
            print("%-10s vektor=%s -> %s (dim model=%d)"
                  % (nama, dims or "(kosong)", "OK" if ok else "PERLU REINDEX",
                     dim_model), flush=True)
    if not do_reindex:
        print("\nJalankan dengan --reindex-all untuk memperbaiki vektor yang mismatch.",
              flush=True)
    panduan_berikutnya()
    return 0


if __name__ == "__main__":
    sys.exit(main())
