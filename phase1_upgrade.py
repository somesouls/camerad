# -*- coding: utf-8 -*-
"""phase1_upgrade.py — Migrasi FTS ternormalisasi (Fase 1) untuk Peraturan & SOP.

Yang dikerjakan skrip ini:
  * Membangun ulang indeks FTS5 ke versi TARGET modul (peraturan_db /
    sop_db.FTS_TARGET_VERSION bila ada; default '2'):
      - v2: konten TERNORMALISASI (lowercase, tanpa diakritik, tanpa stopword;
        + stemming Sastrawi bila paket terpasang) + kolom hierarchy (peraturan).
      - v3 (peraturan, v20): + kolom `nomor` berbobot bm25 tertinggi agar query
        bernomor exact ("PER-23/PJ/2016") cocok secara leksikal.
  * Setelah migrasi, retrieval lexical otomatis memakai bm25 BERBOBOT
    (nomor > judul > hierarchy/bagian > isi) — tidak perlu mengubah kode apa pun.

Aman dijalankan kapan saja (idempoten); TIDAK butuh model embedding / GPU /
koneksi internet; TIDAK menyentuh vektor (tanpa reindex embedding).
Untuk hasil terbaik pasang Sastrawi dulu:  pip install Sastrawi

PENTING: jangan jalankan bersamaan dengan reindex embedding Fase 0
(phase0_upgrade.py) — keduanya menulis ke berkas DB yang sama.

Pemakaian:
  python phase1_upgrade.py            # migrasi indeks yang belum di versi target
  python phase1_upgrade.py --force    # bangun ulang walau sudah di versi target
"""
import argparse
import sys
import time


def _hr(t):
    print("\n" + "=" * 64 + "\n" + t + "\n" + "=" * 64, flush=True)


def _cek_norm():
    try:
        import common.text_norm as tn
        info = tn.info()
        print("text_norm: stemming=%s sastrawi=%s stopwords=%d"
              % (info.get("stem_enabled"), info.get("sastrawi"),
                 info.get("stopwords")), flush=True)
        if not info.get("sastrawi"):
            print("  (Sastrawi belum terpasang -> normalisasi dasar saja; "
                  "untuk stemming: pip install Sastrawi)", flush=True)
    except Exception as e:
        print("[X] text_norm tak dapat diimpor: %s" % e, flush=True)


def _migrasi(nama, mod, force=False):
    target = str(getattr(mod, "FTS_TARGET_VERSION", "2"))
    _hr("MIGRASI FTS (target v%s): %s" % (target, nama))
    try:
        info = mod.fts_info()
    except Exception as e:
        print("[X] fts_info gagal: %s" % e, flush=True)
        return
    print("Sebelum: %s" % info, flush=True)
    if str(info.get("fts_version")) == target and not force:
        print("Sudah v%s — dilewati (pakai --force untuk membangun ulang)."
              % target, flush=True)
        return
    t0 = time.time()
    res = mod.rebuild_fts_norm()
    dt = time.time() - t0
    if isinstance(res, dict) and res.get("ok"):
        print("[OK] %d baris terindeks ulang dalam %.1f dtk." % (res.get("n", 0), dt),
              flush=True)
        try:
            print("Sesudah: %s" % mod.fts_info(), flush=True)
        except Exception:
            pass
    else:
        print("[X] migrasi gagal: %s" % res, flush=True)


def main():
    ap = argparse.ArgumentParser(description="Migrasi FTS ternormalisasi (Fase 1)")
    ap.add_argument("--force", action="store_true",
                    help="bangun ulang indeks walau sudah di versi target")
    args = ap.parse_args()
    _hr("CEK NORMALISASI")
    _cek_norm()
    try:
        import peraturan.db as peraturan_db
        _migrasi("PERATURAN", peraturan_db, force=args.force)
    except Exception as e:
        print("[X] peraturan_db tak dapat diimpor: %s" % e, flush=True)
    try:
        import sop.db as sop_db
        _migrasi("SOP", sop_db, force=args.force)
    except Exception as e:
        print("[X] sop_db tak dapat diimpor: %s" % e, flush=True)
    _hr("SELESAI")
    print("""\
Retrieval lexical kini memakai FTS ternormalisasi + bm25 berbobot
(v3: kolom nomor ikut terindeks — bobot tertinggi — utk query bernomor exact).
Bentuk kata berimbuhan disamakan: menyerahkan/penyerahan/diserahkan.
Uji di /rag-lab, mis.:
  - "ketentuan penyerahan BKP dari luar daerah pabean ke kawasan berikat"
  - "peraturan yang mengatur SPLN"
  - "bunyi pasal 19 PER-23/PJ/2016"
""", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
