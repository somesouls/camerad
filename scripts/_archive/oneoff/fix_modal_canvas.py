#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Perbaiki modal/popup TRANSPARAN pada halaman tools (Analisis Dialogflow, dll).

AKAR MASALAH (sudah dikonfirmasi)
---------------------------------
Di base.html, blok :root "tools set" mendefinisikan:
    --canvas:transparent;
Modal (.modal), dropdown intent (.s6menu), dan beberapa input memakai
background:var(--canvas). Karena nilainya 'transparent', latarnya tembus
pandang menembus halaman di belakangnya. Ini BUKAN token yang hilang -- token
ada, tapi nilainya salah.

Tidak ada elemen lain di base.html yang memakai var(--canvas), jadi mengganti
satu baris ini AMAN dan otomatis memperbaiki SEMUA template tools sekaligus.

PERBAIKAN
---------
    --canvas:transparent;  ->  --canvas:var(--bg-grad-1);

--bg-grad-1 sudah OPAQUE dan sudah punya nilai untuk mode gelap (#0f172a) dan
terang (#e2e8f0), jadi modal jadi solid & tetap mengikuti tema (dark/light).

Aman diulang (idempotent) & membuat backup .bak_modal sekali.

Pakai:
    python fix_modal_canvas.py base.html
    # atau: python fix_modal_canvas.py templates/base.html
"""
import sys, os, shutil, re

NEW = "--canvas:var(--bg-grad-1)"
# cocokkan '--canvas' diikuti ':' (spasi opsional) lalu 'transparent'
PAT = re.compile(r"--canvas\s*:\s*transparent")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Pakai: python fix_modal_canvas.py base.html")
    path = sys.argv[1]
    if not os.path.exists(path):
        raise SystemExit("[GAGAL] file tidak ditemukan: %s" % path)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if NEW in src:
        print("Sudah ter-patch (--canvas:var(--bg-grad-1)). Tidak ada perubahan.")
        return

    matches = PAT.findall(src)
    if not matches:
        raise SystemExit(
            "[GAGAL] pola '--canvas:transparent' tidak ditemukan. "
            "Pastikan ini file base.html yang benar."
        )

    bak = path + ".bak_modal"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print("Backup dibuat: %s" % bak)

    new_src, n = PAT.subn(NEW, src)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print("BERES. %d kemunculan diganti. Modal kini solid & mengikuti tema." % n)
    print("Restart server, lalu buka Analisis Dialogflow -> klik step (modal tak lagi transparan).")


if __name__ == "__main__":
    main()
