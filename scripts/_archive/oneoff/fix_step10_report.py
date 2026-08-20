#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_step10_report.py  --  Perbaiki Step 10 "Buat Laporan" (Analisis Dialogflow).

Masalah: port Python step10_build SALAH domain. Ia hanya membaca sheet
"Analisis MKTA" dan menulis header ala-Dialogflow
(ID trace/user phrase/.../Intent Seharusnya/Catatan) untuk sheet LM &
(Intent Seharusnya/Training Phrase Baru) untuk Pembaruan.

Seharusnya (sesuai versi PHP asli): agregasi PER ID REKAMAN dari 4 sheet
(Non Fallback, Analisis Fallback, QA Conf MKTA, Analisis MKTA) lalu tulis:
  - Sheet LM        : TGL_REKAMAN, NOMOR_REKAMAN, NM_AGENT, HASIL_LM, CATATAN_LM
  - Sheet Pembaruan : INSERT_ID, NAMA_MATERI, TGL PENYUSUNAN, NAMA PENYUSUN,
                      RANGKUMAN, STATUS MATERI, KATEGORI
  - CSV LM        : semua kolom LM
  - CSV Pembaruan : NAMA_MATERI..STATUS MATERI (buang INSERT_ID & KATEGORI)

Patcher ini mengganti seluruh fungsi step10_build dan menambahkan helper
_s10_date. Kode pengganti dibaca dari file 'step10_build_new.py' yang berada
di folder yang sama dengan patcher ini. Idempoten, membuat backup
.bak_step10rep, dan hanya menimpa file asli bila lolos py_compile.

Pakai:  python fix_step10_report.py pipeline_routes.py
"""
import io
import os
import re
import sys
import py_compile
import tempfile

MARKER = "# [fix_step10_report] applied"
HERE = os.path.dirname(os.path.abspath(__file__))
PAYLOAD = os.path.join(HERE, "step10_build_new.py")


def _load_payload():
    if not os.path.isfile(PAYLOAD):
        print("[gagal] File payload tidak ditemukan:", PAYLOAD)
        print("        Pastikan step10_build_new.py ada di folder yang sama.")
        sys.exit(2)
    with io.open(PAYLOAD, "r", encoding="utf-8") as f:
        block = f.read()
    if not block.endswith("\n"):
        block += "\n"
    return block


def main():
    if len(sys.argv) < 2:
        print("Usage: python fix_step10_report.py pipeline_routes.py")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.isfile(path):
        print("File tidak ditemukan:", path)
        sys.exit(1)
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("[skip] Penanda sudah ada -- patch step10_report sepertinya sudah terpasang.")
        _compile_check(path)
        return

    block = _load_payload()

    m = re.search(r"\ndef step10_build\(cfg, ctx\):\n", src)
    if not m:
        print("[gagal] Tidak menemukan 'def step10_build(cfg, ctx):'. Batalkan.")
        sys.exit(2)
    start = m.start() + 1  # posisi awal 'def'

    # Cari akhir fungsi: paling andal = awal blok komentar STEP 11
    e = re.search(r"\n# =+\n# STEP 11", src[start:])
    if e:
        end = start + e.start() + 1  # sisakan satu newline sebelum blok komentar
    else:
        e2 = re.search(r"\ndef [A-Za-z_]", src[m.end():])
        if not e2:
            print("[gagal] Tidak menemukan batas akhir fungsi step10_build.")
            sys.exit(2)
        end = m.end() + e2.start() + 1

    new_src = src[:start] + block + "\n" + src[end:]

    # Backup
    bak = path + ".bak_step10rep"
    if not os.path.exists(bak):
        with io.open(bak, "w", encoding="utf-8") as f:
            f.write(src)
        print("[ok] Backup dibuat:", bak)

    # Tulis ke tempfile lalu py_compile sebelum menimpa asli
    fd, tmp = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(new_src)
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as ex:
        print("[gagal] Hasil patch tidak lolos py_compile, file asli TIDAK diubah.")
        print(ex)
        os.remove(tmp)
        sys.exit(3)

    with io.open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    os.remove(tmp)
    print("[ok] step10_build diganti dengan port setia versi PHP.")
    _compile_check(path)


def _compile_check(path):
    try:
        py_compile.compile(path, doraise=True)
        print("[ok] py_compile OK:", path)
    except py_compile.PyCompileError as ex:
        print("[PERINGATAN] py_compile gagal:", ex)
        sys.exit(4)


if __name__ == "__main__":
    main()
