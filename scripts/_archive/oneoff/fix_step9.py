#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_step9.py - Perbaiki Step 9 (pipeline_routes.py) agar tabel SELALU
menampilkan baris yang sudah punya PUTUSAN dari Step 8, walaupun threshold
yang dipakai Step 9 berbeda dari Step 8.

Masalah: step9_load()/step9_save() menyaring baris HANYA dengan
'Skor Pemrosesan Bahasa < threshold' (default 0.6). Bila Step 8 dijalankan
dengan threshold lain (mis. 0.7), baris yang sudah diputus Step 8 tetapi
skornya >= threshold Step 9 akan TERSEMBUNYI -> tabel Step 9 kosong dari
data Step 8.

Perbaikan: tampilkan baris bila (di bawah threshold) ATAU (sudah ada PUTUSAN).

Pakai:  python fix_step9.py [path/ke/pipeline_routes.py]
"""
import io, os, sys, py_compile

PATH = sys.argv[1] if len(sys.argv) > 1 else "pipeline_routes.py"
if not os.path.isfile(PATH):
    print("File tidak ditemukan:", PATH); sys.exit(1)

orig = io.open(PATH, encoding="utf-8").read()
src = orig

PAIRS = [
    (  # --- step9_load ---
'''        sc = _sv(cells, col_score)
        if not (_is_numeric(sc) and float(sc) < threshold):
            continue
        _id = _sv(cells, col_id)
''',
'''        sc = _sv(cells, col_score)
        _put_now = _sv(cells, col_put).strip()
        _below = _is_numeric(sc) and float(sc) < threshold
        # Tampilkan baris di bawah threshold ATAU yang SUDAH punya PUTUSAN dari
        # Step 8, supaya hasil Step 8 selalu muncul walau threshold berbeda.
        if not _below and _put_now == "":
            continue
        _id = _sv(cells, col_id)
'''),
    (  # --- step9_save ---
'''        sc = _sv(cells, col_score)
        if not (_is_numeric(sc) and float(sc) < threshold):
            continue
        e = edit_map.get(rn, {})
''',
'''        sc = _sv(cells, col_score)
        _put_now = _sv(cells, col_put).strip()
        _below = _is_numeric(sc) and float(sc) < threshold
        if not _below and _put_now == "":
            continue
        e = edit_map.get(rn, {})
'''),
]

done = 0
for i, (old, new) in enumerate(PAIRS, 1):
    if new in src and old not in src:
        print("[%d] sudah dipatch sebelumnya, dilewati." % i); done += 1; continue
    n = src.count(old)
    if n != 1:
        print("[%d] GAGAL: ditemukan %d kecocokan (harus tepat 1). Tidak ada perubahan ditulis." % (i, n))
        sys.exit(2)
    src = src.replace(old, new, 1)
    print("[%d] OK dipatch." % i); done += 1

if src == orig:
    print("Tidak ada perubahan (mungkin sudah dipatch semua). Selesai.")
    sys.exit(0)

bak = PATH + ".bak"
if not os.path.exists(bak):
    io.open(bak, "w", encoding="utf-8").write(orig)
    print("Backup asli ->", bak)
io.open(PATH, "w", encoding="utf-8").write(src)
try:
    py_compile.compile(PATH, doraise=True)
    print("py_compile OK.")
except Exception as e:
    print("PERINGATAN py_compile:", e)
print("Selesai. %d blok diproses." % done)
