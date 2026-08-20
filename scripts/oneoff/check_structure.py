# -*- coding: utf-8 -*-
"""Guard struktur repo (dipakai CI & pre-commit).

1) py_compile semua .py aplikasi (tanpa deps pihak-ketiga; interpreter-agnostic).
2) Cegah regresi: tidak boleh ada shim baru di ROOT
   (berkas *.py di root yang memuat 'sys.modules[__name__]').
Keluar !=0 bila ada pelanggaran.
"""
import os
import sys
import py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "_legacy", "_archive"}
SHIM_MARKER = "sys.modules[__name__]"


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(base, fn)


def main():
    errs = []
    for p in py_files():
        try:
            py_compile.compile(p, doraise=True)
        except py_compile.PyCompileError as e:
            msg = str(e).strip().splitlines()
            errs.append("COMPILE-FAIL %s: %s" % (rel(p), msg[-1] if msg else "error"))
    for name in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, name)
        if not (name.endswith(".py") and os.path.isfile(p)):
            continue
        try:
            with open(p, "rb") as f:
                txt = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        if SHIM_MARKER in txt:
            errs.append("ROOT-SHIM (dilarang, sudah dimigrasi): %s" % name)
    if errs:
        print("[guard] GAGAL:")
        for e in errs:
            print("  - " + e)
        return 1
    print("[guard] OK: semua .py compile & tidak ada shim di root.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
