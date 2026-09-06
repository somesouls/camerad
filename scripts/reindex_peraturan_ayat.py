# -*- coding: utf-8 -*-
"""reindex_peraturan_ayat.py — Reindex granularitas PER-AYAT untuk peraturan.db.

Latar belakang
--------------
Retrieval peraturan (peraturan/db.py) SUDAH hybrid: FTS5/BM25 (leksikal,
ternormalisasi + bm25 berbobot kolom) + vektor dense bge-m3 (semantik),
digabung Reciprocal Rank Fusion (RRF). Korpus juga sudah tersimpan per-unit
(tabel peraturan_unit punya kolom `pasal` dan `ayat`).

Namun sebagian unit ter-ingest setingkat PASAL: satu baris berisi seluruh ayat
pada pasal itu di kolom `isi` (mis. "(1) ... (2) ... (3) ..."), sementara kolom
`ayat` kosong. Granularitas kasar ini menurunkan presisi retrieval:
  * satu blok pasal panjang bersaing sebagai SATU kandidat tunggal (satu skor
    RRF), sehingga ayat spesifik yang relevan sulit "menang" atas pasal lain;
  * pemotongan isi per-blok (_ctx_peraturan `_clip`, mis. 1300 char untuk
    profil agent) dapat MEMBUANG ayat yang justru dicari;
  * embedding satu vektor untuk seluruh pasal mengaburkan makna tiap ayat.

Solusi (skrip ini)
------------------
Memecah unit pasal multi-ayat menjadi unit PER-AYAT (child), lalu meng-upsert
tiap child via peraturan.db.upsert_peraturan() — yang otomatis (a) meng-embed
ulang teks child (vektor dense per-ayat) dan (b) menyinkronkan indeks FTS.

Sifat & keamanan
----------------
  * OFFLINE / admin-run. TIDAK mengubah kode runtime; retrieval memakai skema &
    fungsi yang sama (search() hybrid + RRF), hanya granularitas datanya membaik.
  * DRY-RUN secara default: hanya melaporkan rencana. Menulis HANYA dengan --apply.
  * Idempoten: id child deterministik ("<id_induk>#ayat-<n>"). Menjalankan ulang
    hanya memperbarui child yang sama, tidak menggandakan.
  * Konservatif: hanya memecah bila terdeteksi >= PECAH_MIN_AYAT penanda ayat
    berurutan mulai dari (1). Bila ragu, unit dilewati (tak diubah).
  * Baris induk: secara default DIPERTAHANKAN (aman) — gunakan --hapus-induk
    untuk menghapusnya agar tak muncul ganda dengan child-nya.
  * Gagal-anggun: kegagalan per-unit dicatat & dilewati, tidak menggagalkan
    seluruh proses.

SELALU cadangkan peraturan.db sebelum --apply. Jangan jalankan bersamaan dengan
batch/reindex embedding lain (menulis DB yang sama; lihat catatan konkurensi di
peraturan/db.py).

Contoh
------
    # 1) Pratinjau (tidak menulis apa pun):
    python scripts/reindex_peraturan_ayat.py

    # 2) Pratinjau satu peraturan tertentu, lebih rinci:
    python scripts/reindex_peraturan_ayat.py --nomor "PER-23/PJ/2016" --verbose

    # 3) Terapkan (memecah + re-embed child), induk tetap dipertahankan:
    python scripts/reindex_peraturan_ayat.py --apply

    # 4) Terapkan + hapus baris induk yang sudah dipecah:
    python scripts/reindex_peraturan_ayat.py --apply --hapus-induk

Env terkait embedding (dipakai peraturan.semantic saat upsert):
    PERATURAN_EMBED=1  PERATURAN_EMBED_MODEL=BAAI/bge-m3  PERATURAN_EMBED_DEVICE=
"""
import os
import re
import sys
import json
import argparse

# Pastikan root repo ada di sys.path saat dijalankan dari mana pun.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import peraturan.db as pdb  # noqa: E402


# Penanda ayat di awal segmen: "(1)", "(2)", "(1a)" dst. Kita hanya memecah bila
# penanda BERURUTAN mulai dari 1 agar tidak salah pecah pada rujukan "(2)" yang
# muncul di tengah kalimat.
_AYAT_RE = re.compile(r"\((\d+[a-z]?)\)\s", re.UNICODE)


def _min_ayat():
    try:
        return max(2, int(os.environ.get("PECAH_MIN_AYAT", "2")))
    except Exception:
        return 2


def _num_only(s):
    m = re.match(r"(\d+)", str(s or ""))
    return int(m.group(1)) if m else None


def pecah_ayat(isi):
    """Pecah teks `isi` menjadi [(label_ayat, teks_ayat), ...] bila memuat
    penanda ayat berurutan mulai (1). Kembalikan [] bila tak layak dipecah."""
    isi = (isi or "").strip()
    if not isi:
        return []
    marks = list(_AYAT_RE.finditer(isi))
    if len(marks) < _min_ayat():
        return []
    # Penanda pertama harus "(1)" dan nomor (bagian angka) harus non-menurun &
    # dimulai dari 1 — tanda kuat bahwa ini daftar ayat, bukan rujukan lepas.
    labels = [m.group(1) for m in marks]
    nums = [_num_only(x) for x in labels]
    if nums[0] != 1 or any(n is None for n in nums):
        return []
    for a, b in zip(nums, nums[1:]):
        if b < a:               # harus monoton naik (boleh sama utk 1,1a)
            return []
    segs = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(isi)
        teks = isi[start:end].strip()
        if teks:
            segs.append((labels[i], teks))
    return segs if len(segs) >= _min_ayat() else []


def _child_id(induk_id, label):
    return "%s#ayat-%s" % (induk_id, label)


def _kandidat(conn, nomor=None, jenis=None, limit=None):
    """Ambil unit setingkat pasal yang ISInya multi-ayat & kolom `ayat` kosong."""
    sql = ("SELECT * FROM peraturan_unit "
           "WHERE COALESCE(TRIM(ayat),'')='' "
           "AND COALESCE(TRIM(pasal),'')<>'' "
           "AND isi IS NOT NULL AND TRIM(isi)<>''")
    args = []
    if nomor:
        sql += " AND nomor=?"
        args.append(nomor)
    if jenis:
        sql += " AND jenis_peraturan=?"
        args.append(jenis)
    rows = conn.execute(sql, tuple(args)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        segs = pecah_ayat(d.get("isi"))
        if segs:
            out.append((d, segs))
            if limit and len(out) >= limit:
                break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Reindex granularitas per-ayat untuk peraturan.db (dry-run "
                    "secara default).")
    ap.add_argument("--apply", action="store_true",
                    help="Tulis perubahan ke DB (default: dry-run saja).")
    ap.add_argument("--hapus-induk", action="store_true",
                    help="Hapus baris induk pasal setelah child dibuat.")
    ap.add_argument("--nomor", default=None, help="Batasi ke satu nomor peraturan.")
    ap.add_argument("--jenis", default=None, help="Batasi ke satu jenis peraturan.")
    ap.add_argument("--limit", type=int, default=0, help="Batasi jumlah unit induk.")
    ap.add_argument("--verbose", action="store_true", help="Tampilkan detail per unit.")
    args = ap.parse_args(argv)

    limit = args.limit if args.limit and args.limit > 0 else None
    conn = pdb.init_db(pdb.connect())
    try:
        kandidat = _kandidat(conn, nomor=args.nomor, jenis=args.jenis, limit=limit)
    except Exception as e:
        print("[reindex-ayat] gagal membaca kandidat:", e, flush=True)
        conn.close()
        return 2

    total_induk = len(kandidat)
    total_child = sum(len(segs) for _, segs in kandidat)
    print("=" * 70)
    print(" Reindex per-ayat peraturan.db  —  %s"
          % ("APPLY (menulis DB)" if args.apply else "DRY-RUN (tanpa menulis)"))
    print("  DB              : %s" % pdb.default_db_path())
    print("  Unit induk cocok: %d" % total_induk)
    print("  Calon child     : %d ayat" % total_child)
    print("  Min ayat pecah  : %d" % _min_ayat())
    print("  Hapus induk     : %s" % ("ya" if args.hapus_induk else "tidak"))
    print("=" * 70)

    if args.verbose or not args.apply:
        for d, segs in kandidat[:200]:
            head = " ".join(x for x in [d.get("jenis_peraturan") or "",
                                        d.get("nomor") or "",
                                        "Pasal " + str(d.get("pasal") or "")] if x).strip()
            print("  • %s  [id=%s]  -> %d ayat" % (head, d.get("id"), len(segs)))
            if args.verbose:
                for lbl, teks in segs:
                    print("      ({}) {}".format(lbl, (teks[:90] + "…") if len(teks) > 90 else teks))

    if not args.apply:
        print("\n[reindex-ayat] DRY-RUN selesai. Jalankan dengan --apply untuk menulis.")
        print("[reindex-ayat] INGAT: cadangkan peraturan.db sebelum --apply.")
        conn.close()
        return 0

    if not pdb.psem.is_available():
        print("[reindex-ayat] PERINGATAN: model embedding TIDAK tersedia — child "
              "akan dibuat TANPA vektor dense (FTS tetap tersinkron). Jalankan "
              "'python phase0_upgrade.py --reindex-all' setelahnya untuk mengisi vektor.",
              flush=True)

    n_ok = n_child = n_hapus = n_gagal = 0
    for d, segs in kandidat:
        induk_id = d.get("id")
        try:
            for lbl, teks in segs:
                child = dict(d)
                child["id"] = _child_id(induk_id, lbl)
                child["ayat"] = lbl
                child["isi"] = teks
                # Perkaya hierarchy agar tampil jelas di rujukan.
                h = str(d.get("hierarchy") or "").strip()
                suff = "Ayat (%s)" % lbl
                child["hierarchy"] = (h + " › " + suff) if h else suff
                child["source_id"] = d.get("source_id")
                pdb.upsert_peraturan(child, conn=conn)
                n_child += 1
            if args.hapus_induk:
                pdb.delete_peraturan(induk_id, conn=conn)
                n_hapus += 1
            n_ok += 1
            if n_ok % 50 == 0:
                print("[reindex-ayat] %d/%d induk diproses (%d child)"
                      % (n_ok, total_induk, n_child), flush=True)
        except Exception as e:
            n_gagal += 1
            print("[reindex-ayat] gagal pada id=%s: %s" % (induk_id, str(e)[:160]),
                  flush=True)

    # Sinkronkan ulang indeks FTS agar konsisten (aman & idempoten).
    try:
        info = pdb.rebuild_fts_norm(conn=conn, progress=False)
        print("[reindex-ayat] rebuild FTS:", json.dumps(info, ensure_ascii=False))
    except Exception as e:
        print("[reindex-ayat] rebuild FTS dilewati:", str(e)[:160])

    conn.close()
    print("=" * 70)
    print("[reindex-ayat] SELESAI. induk=%d child=%d hapus_induk=%d gagal=%d"
          % (n_ok, n_child, n_hapus, n_gagal))
    print("[reindex-ayat] Verifikasi via menu Peraturan / Uji Cepat di Konfigurasi RAG Agent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
