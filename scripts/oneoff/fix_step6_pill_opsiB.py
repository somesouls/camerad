#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frontend companion Opsi B: templates/tools.html
Menampilkan kategori baru "TIDAK LAYAK TRAINING" (label 7) di tabel Step 6
dengan pill merah agar mudah dibedakan & difilter analis.

Aman diulang (idempotent), buat .bak sekali.
Pakai:  python fix_step6_pill_opsiB.py templates/tools.html
"""
import sys, os, shutil

EDITS = [
    ("css pill .x",
     '.s6pill.n{background:var(--soft2); color:var(--text2)}',
     '.s6pill.n{background:var(--soft2); color:var(--text2)}\n'
     '.s6pill.x{background:var(--red-soft); color:var(--red)}',
     '.s6pill.x{'),

    ("js pill mapping",
     "const pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':'m');",
     "const pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':(r.catatan==='TIDAK LAYAK TRAINING'?'x':'m'));",
     "'TIDAK LAYAK TRAINING'?'x'"),
]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_step6_pill_opsiB.py templates/tools.html")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    bak = path + ".bak_opsib"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)
    for label, old, new, marker in EDITS:
        if marker in src:
            print("  [lewati] %s (sudah ada)" % label); continue
        if old not in src:
            raise SystemExit("  [GAGAL] anchor '%s' tidak ditemukan." % label)
        src = src.replace(old, new, 1)
        print("  [ok] %s" % label)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("BERES. %s ter-patch." % path)


if __name__ == "__main__":
    main()
