#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch templates/tools.html: modal (dan seluruh UI pipeline) TRANSPARAN.

AKAR MASALAH
------------
tools.html memakai variabel CSS --canvas, --soft, --soft2, --border, --text,
--text2, --blue, dst, TAPI variabel itu TIDAK didefinisikan (base.html hanya
punya --panel-bg, --text-main, --accent, dsb). Akibatnya .modal{background:
var(--canvas)} menjadi TIDAK VALID -> latar modal transparan, menembus halaman
di belakangnya.

PERBAIKAN
---------
Menyuntik blok :root berisi definisi token yang dipakai tools.html, dengan
nilai SOLID (opaque) + varian light. Tidak menimpa token base.html (nama beda).

Aman diulang (idempotent) & membuat .bak sekali.

Pakai:
    python fix_modal_transparan.py templates/tools.html
"""
import sys, os, shutil

ANCHOR = '{% block head %}<style>\n'
MARKER = '/* == token tools.html (fix modal transparan) == */'

TOKENS = ANCHOR + MARKER + '''
:root{
  --canvas:#0b1220; --soft:#111a2e; --soft2:#1a2540; --border:#2a3550;
  --text:#f1f5f9; --text2:#94a3b8;
  --blue:#3b82f6; --blue-soft:rgba(59,130,246,.18);
  --green:#10b981; --green-soft:rgba(16,185,129,.18);
  --red:#ef4444;   --red-soft:rgba(239,68,68,.18);
  --orange:#f59e0b; --orange-soft:rgba(245,158,11,.18);
  --radius:16px; --shadow:0 24px 60px rgba(0,0,0,.5);
}
[data-theme="light"]{
  --canvas:#ffffff; --soft:#f8fafc; --soft2:#eef2f7; --border:#e2e8f0;
  --text:#0f172a; --text2:#475569;
  --shadow:0 24px 60px rgba(2,6,23,.18);
}
/* backdrop lebih pekat + modal solid */
.overlay{background:rgba(2,6,23,.66)!important; backdrop-filter:blur(4px)}
.modal{background:var(--canvas)!important}
/* == end token tools.html == */
'''


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_modal_transparan.py templates/tools.html")
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("Sudah ter-patch. Tidak ada perubahan.")
        return
    if ANCHOR not in src:
        raise SystemExit("[GAGAL] anchor '{% block head %}<style>' tidak ditemukan.")

    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)

    src = src.replace(ANCHOR, TOKENS, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("BERES. %s ter-patch (modal kini solid/opaque)." % path)


if __name__ == "__main__":
    main()
