#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_step9_load_only.py — HANYA perbaikan step9_load (opsional).

Menampilkan baris Step 9 yang di bawah threshold ATAU yang SUDAH punya PUTUSAN
dari Step 8, supaya hasil Step 8 selalu muncul walau threshold berbeda.

Dipisah dari fix_step9.py karena bagian step9_save sudah diganti oleh perbaikan
"Data Edit Tidak Valid" sebelumnya. Patcher ini IDEMPOTEN dan TIDAK fatal:
kalau sudah ter-patch atau target tak ditemukan, ia keluar dengan kode 0.

Pakai:  python fix_step9_load_only.py pipeline_routes.py
"""
import io, os, sys, py_compile

PATH = sys.argv[1] if len(sys.argv) > 1 else "pipeline_routes.py"
if not os.path.isfile(PATH):
    print("[skip] File tidak ditemukan:", PATH); sys.exit(0)

OLD = (
    '        sc = _sv(cells, col_score)\n'
    '        if not (_is_numeric(sc) and float(sc) < threshold):\n'
    '            continue\n'
    '        _id = _sv(cells, col_id)\n'
)
NEW = (
    '        sc = _sv(cells, col_score)\n'
    '        _put_now = _sv(cells, col_put).strip()\n'
    '        _below = _is_numeric(sc) and float(sc) < threshold\n'
    '        # Tampilkan baris di bawah threshold ATAU yang SUDAH punya PUTUSAN dari\n'
    '        # Step 8, supaya hasil Step 8 selalu muncul walau threshold berbeda.\n'
    '        if not _below and _put_now == "":\n'
    '            continue\n'
    '        _id = _sv(cells, col_id)\n'
)

orig = io.open(PATH, encoding="utf-8").read()
if NEW in orig:
    print("[skip] step9_load sudah ter-patch."); sys.exit(0)
n = orig.count(OLD)
if n == 0:
    print("[skip] Target step9_load tidak ditemukan (mungkin versi berbeda) — dilewati aman."); sys.exit(0)
if n != 1:
    print("[skip] Ditemukan %d kecocokan (harus tepat 1) — dilewati aman." % n); sys.exit(0)

src = orig.replace(OLD, NEW, 1)
bak = PATH + ".bak_step9load"
if not os.path.exists(bak):
    io.open(bak, "w", encoding="utf-8").write(orig)
    print("Backup ->", bak)
io.open(PATH, "w", encoding="utf-8").write(src)
try:
    py_compile.compile(PATH, doraise=True)
    print("[ok] step9_load dipatch + py_compile OK.")
except Exception as e:
    io.open(PATH, "w", encoding="utf-8").write(orig)
    print("[batal] py_compile gagal, dikembalikan:", e); sys.exit(0)
