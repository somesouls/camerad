# -*- coding: utf-8 -*-
"""
reindex_context.py — Re-embedding KONTEKSTUAL untuk korpus peraturan.

Masalah yang diatasi (perbaikan "chunking")
-------------------------------------------
Saat ini vektor e5 tiap unit dibangun dari teks:  judul + isi
(lihat peraturan_db._sync_vec dan peraturan_db.reindex). Padahal kolom
peraturan_unit menyimpan KONTEKS STRUKTURAL secara terpisah: jenis_peraturan,
nomor, tahun, bab, bagian, paragraf, pasal, ayat, dan 'hierarchy'. Konteks itu
HILANG dari vektor, sehingga potongan berbunyi mis. "Dihapus." atau
"sebagaimana dimaksud pada ayat (1)" jadi nyaris tak punya sinyal semantik dan
sulit ditemukan e5 dari pertanyaan user yang informal.

Script ini membangun ULANG vektor dari teks KONTEKSTUAL:
    [judul | identitas peraturan | hierarki/bab/bagian/pasal] + isi
sehingga tiap potongan "tahu" ia bagian peraturan & pasal mana. Ini biasanya
menaikkan recall retrieval untuk teks hukum yang saling berkaitan.

Sifat & keamanan
----------------
- HANYA menyentuh tabel peraturan_vec (embedding). Tidak mengubah teks/isi
  peraturan_unit, tidak mengubah skema, tidak mengubah perilaku engine.
- Reversibel: untuk kembali ke perilaku lama jalankan:
      python -c "import peraturan_db; print(peraturan_db.reindex())"
- Ukur dampaknya dengan eval_retrieval.py (recall@k) SEBELUM vs SESUDAH.

Cara pakai
----------
  python reindex_context.py --dry-run          # intip teks kontekstual 3 baris
  python reindex_context.py                     # re-embed seluruh korpus
  python reindex_context.py --limit 500         # sebagian (uji)
  PERATURAN_CTX_MAXCHAR=1800 python reindex_context.py

Catatan: perlu model embedding tersedia (PERATURAN_EMBED=1 + sentence-
transformers). Proses bisa lama untuk korpus besar; jalankan saat tidak ada
batch/reindex lain (SQLite hanya izinkan satu penulis).
"""
import os
import sys
import argparse

import peraturan_db as pdb
import peraturan_semantic as psem


def _maxchar():
    try:
        return int(os.environ.get("PERATURAN_CTX_MAXCHAR", "1800"))
    except Exception:
        return 1800


_COLS = ("id", "jenis_peraturan", "nomor", "tahun", "judul",
         "bab", "bagian", "paragraf", "pasal", "ayat", "hierarchy", "isi")


def build_context_text(row, maxchar=None):
    """Susun teks kontekstual: header identitas + hierarki + isi."""
    maxchar = maxchar or _maxchar()
    keys = set(row.keys())
    g = lambda k: (row[k] if (k in keys and row[k] is not None) else "")
    # 1) identitas peraturan
    ident_bits = []
    if g("jenis_peraturan"):
        ident_bits.append(str(g("jenis_peraturan")))
    if g("nomor"):
        ident_bits.append("Nomor %s" % g("nomor"))
    if g("tahun"):
        ident_bits.append("Tahun %s" % g("tahun"))
    ident = " ".join(ident_bits)
    # 2) hierarki struktural (pakai kolom 'hierarchy' bila ada, else rakit)
    hir = str(g("hierarchy") or "").strip()
    if not hir:
        hb = []
        for lbl, key in (("Bab", "bab"), ("Bagian", "bagian"),
                         ("Paragraf", "paragraf"), ("Pasal", "pasal"),
                         ("Ayat", "ayat")):
            if g(key):
                hb.append("%s %s" % (lbl, g(key)))
        hir = " > ".join(hb)
    # 3) rakit header + isi
    head = []
    if g("judul"):
        head.append(str(g("judul")).strip())
    if ident:
        head.append(ident)
    if hir:
        head.append(hir)
    header = " | ".join(head)
    isi = str(g("isi") or "").strip()
    teks = (header + "\n" + isi).strip() if header else isi
    if maxchar and len(teks) > maxchar:
        teks = teks[:maxchar]
    return teks


def reindex_context(conn=None, batch=64, limit=0, dry_run=False, maxchar=None):
    own = conn is None
    conn = conn or pdb.init_db(pdb.connect())
    try:
        if not dry_run and not psem.is_available():
            return {"ok": False, "error": "Model embedding tidak tersedia.", "n": 0}
        sql = "SELECT %s FROM peraturan_unit" % ",".join(_COLS)
        if limit:
            sql += " LIMIT %d" % int(limit)
        rows = conn.execute(sql).fetchall()
        ids = [r["id"] for r in rows]
        texts = [build_context_text(r, maxchar=maxchar) for r in rows]

        if dry_run:
            print("DRY-RUN - %d baris. Contoh teks kontekstual:" % len(rows))
            for i in range(min(3, len(rows))):
                print("\n--- id=%s ---\n%s" % (ids[i], texts[i]))
            return {"ok": True, "n": 0, "dry_run": True, "total": len(rows)}

        n = 0
        for i in range(0, len(ids), batch):
            cids = ids[i:i + batch]
            ctxt = texts[i:i + batch]
            arr = psem.embed_passages(ctxt)
            if arr is None:
                continue
            for j, id_ in enumerate(cids):
                v = arr[j]
                conn.execute("DELETE FROM peraturan_vec WHERE id=?", (id_,))
                conn.execute(
                    "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?,?,?)",
                    (id_, int(len(v)), psem.to_blob(v)),
                )
                n += 1
            conn.commit()
            sys.stderr.write("... %d/%d\n" % (min(i + batch, len(ids)), len(ids)))
        try:
            pdb._vec_cache_clear()
        except Exception:
            pass
        return {"ok": True, "n": n, "total": len(ids)}
    finally:
        if own:
            conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-embedding kontekstual korpus peraturan.")
    ap.add_argument("--dry-run", action="store_true", help="Cetak contoh teks tanpa menulis DB.")
    ap.add_argument("--limit", type=int, default=0, help="Batasi jumlah baris (0=semua).")
    ap.add_argument("--batch", type=int, default=64, help="Ukuran batch embedding.")
    ap.add_argument("--maxchar", type=int, default=0, help="Batas panjang teks (0=pakai env/1800).")
    args = ap.parse_args(argv)
    res = reindex_context(batch=args.batch, limit=args.limit,
                          dry_run=args.dry_run, maxchar=(args.maxchar or None))
    print(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
