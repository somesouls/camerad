# -*- coding: utf-8 -*-
"""phase4_mine.py — Rutinkan penambangan feedback -> kurasi -> golden set.

Tahap 3 #3: operasionalisasi loop
    mine_feedback()  ->  kurasi manusia  ->  upsert_golden()  ->  mirror_to_eval()

Alur dua fase (human-in-the-loop; kandidat TIDAK pernah masuk gerbang tanpa
persetujuan admin):

  1. EKSPOR kandidat dari log produksi (jempol-down / jawaban fallback):
        python phase4_mine.py --export kandidat_golden.json [--limit 400]
     -> menulis berkas review JSON. Kandidat yang SUDAH ada di golden set
        ditandai "sudah_ada": true dan "keep": false secara default.

  2. KURASI: admin buka berkas, untuk tiap kandidat yang layak set
        "keep": true, isi "jenis_harapan" ("hit"/"abstain"), dan "expect"
        ({"nomor":[...], "keywords":[...], "gold": "..."} untuk hit).

  3. IMPOR yang disetujui + cermin ke /rag-eval:
        python phase4_mine.py --import kandidat_golden.json
     -> upsert_golden() untuk tiap keep=true, lalu mirror_to_eval()
        (lewati mirror dengan --no-mirror; pratinjau dengan --dry-run).

Lihat cepat tanpa berkas:
        python phase4_mine.py --list [--limit 50]

Stdlib-only. Tanpa f-string (kompat gaya proyek). Fail-open pada penambangan.
"""
import sys
import json
import argparse

import rag.golden_db as g


def _mined(limit):
    res = g.mine_feedback(limit=limit)
    if not res.get("ok"):
        sys.stderr.write("[phase4_mine] mine_feedback gagal: %s\n"
                         % res.get("error"))
        return []
    return res.get("items") or []


def _existing_ids():
    ids = set()
    for row in g.list_golden():
        ids.add(row["id"])
    return ids


def cmd_list(args):
    items = _mined(args.limit)
    if not items:
        print("[phase4_mine] tak ada kandidat feedback.")
        return 0
    exist = _existing_ids()
    print("== KANDIDAT FEEDBACK (mine_feedback, limit=%d) ==" % args.limit)
    for it in items:
        gid = g.gid(it["question"])
        tanda = "  [sudah di golden]" if gid in exist else ""
        print("- down=%d fallback=%d total=%d | %s%s"
              % (it.get("n_down", 0), it.get("n_fallback", 0),
                 it.get("n_total", 0), it["question"], tanda))
    print("total kandidat: %d" % len(items))
    return 0


def cmd_export(args):
    items = _mined(args.limit)
    exist = _existing_ids()
    out = []
    n_baru = 0
    for it in items:
        gid = g.gid(it["question"])
        sudah = gid in exist
        if not sudah:
            n_baru += 1
        catatan = ("mined: n_down=%d n_fallback=%d n_total=%d last_ts=%s"
                   % (it.get("n_down", 0), it.get("n_fallback", 0),
                      it.get("n_total", 0), it.get("last_ts", "")))
        out.append({
            "query": it["question"],
            "keep": False,
            "sudah_ada": sudah,
            "jenis_harapan": "hit",
            "expect": {"nomor": [], "keywords": [], "gold": ""},
            "catatan": catatan,
        })
    payload = {"versi": 1, "sumber": "mine_feedback",
               "total": len(out), "baru": n_baru, "kandidat": out}
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[phase4_mine] ekspor %d kandidat (%d baru) -> %s"
          % (len(out), n_baru, args.path))
    print("Kurasi: set \"keep\": true + isi jenis_harapan/expect, lalu "
          "jalankan: python phase4_mine.py --import %s" % args.path)
    return 0


def _valid_expect(jh, expect):
    if jh == "abstain":
        return True, ""
    ex = expect or {}
    nomor = ex.get("nomor") or []
    kw = ex.get("keywords") or []
    if not nomor and not kw:
        return False, "hit tanpa nomor/keywords"
    return True, ""


def cmd_import(args):
    with open(args.path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    kandidat = payload.get("kandidat") or []
    n_ok = 0
    n_lewat = 0
    n_tolak = 0
    for it in kandidat:
        if not it.get("keep"):
            n_lewat += 1
            continue
        query = (it.get("query") or "").strip()
        if not query:
            n_lewat += 1
            continue
        jh = (it.get("jenis_harapan") or "hit").strip().lower()
        if jh not in ("hit", "abstain"):
            sys.stderr.write("[tolak] jenis_harapan tak sah (%s): %s\n"
                             % (jh, query))
            n_tolak += 1
            continue
        ok, alasan = _valid_expect(jh, it.get("expect"))
        if not ok:
            sys.stderr.write("[tolak] %s: %s\n" % (alasan, query))
            n_tolak += 1
            continue
        if args.dry_run:
            print("[dry-run] akan upsert (%s): %s" % (jh, query))
            n_ok += 1
            continue
        g.upsert_golden(query, jenis_harapan=jh,
                        expect=it.get("expect") or {},
                        catatan=it.get("catatan") or "")
        n_ok += 1
    print("[phase4_mine] impor: %d di-upsert, %d dilewati, %d ditolak"
          % (n_ok, n_lewat, n_tolak))
    if args.dry_run:
        print("(dry-run: tak ada perubahan ditulis; mirror dilewati)")
        return 0
    if n_ok and not args.no_mirror:
        res = g.mirror_to_eval()
        print("[phase4_mine] mirror_to_eval -> %s" % res)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Tahap 3 #3: mine -> kurasi -> golden set.")
    p.add_argument("--limit", type=int, default=400,
                   help="batas baris log yang ditambang (default 400).")
    p.add_argument("--list", action="store_true",
                   help="tampilkan kandidat ke layar (tanpa menulis berkas).")
    p.add_argument("--export", dest="export_path", metavar="FILE",
                   help="tulis berkas review JSON kandidat.")
    p.add_argument("--import", dest="import_path", metavar="FILE",
                   help="baca berkas review; upsert keep=true + mirror.")
    p.add_argument("--no-mirror", action="store_true",
                   help="jangan panggil mirror_to_eval saat impor.")
    p.add_argument("--dry-run", action="store_true",
                   help="impor: tampilkan tanpa menulis / mirror.")
    args = p.parse_args(argv)

    if args.export_path:
        args.path = args.export_path
        return cmd_export(args)
    if args.import_path:
        args.path = args.import_path
        return cmd_import(args)
    if args.list:
        return cmd_list(args)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
