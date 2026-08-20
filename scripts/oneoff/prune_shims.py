# -*- coding: utf-8 -*-
"""Analisis shim kompatibilitas root yang SUDAH TIDAK dipakai.

Shim = berkas .py di ROOT repo yang mengalihkan dirinya ke modul paket via
`sys.modules[__name__] = ...`. Skrip ini memetakan siapa yang masih
mereferensikan tiap shim (import statis, dynamic __import__/import_module, dan
launcher .bat/.sh/Dockerfile/compose), lalu melaporkan shim yang referensinya NOL.

Mode:
  (default)  laporan lengkap DRY-RUN (read-only, tidak menghapus apa pun)
  --list     cetak HANYA nama berkas shim mati (satu per baris) utk skrip lain
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # scripts/oneoff/ -> root repo

SHIM_MARKERS = ("sys.modules[__name__]",)
# Jangan pernah anggap berkas ini sebagai shim (pengaman ekstra).
NEVER = {"web_app.py", "app_core.py"}

CODE_EXT = (".py",)
LAUNCH_EXT = (".sh", ".bat", ".yml", ".yaml", ".cfg", ".ini", ".toml")
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "_legacy"}


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def list_root_shims():
    out = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name in NEVER:
            continue
        p = os.path.join(ROOT, name)
        if os.path.isfile(p) and any(m in _read(p) for m in SHIM_MARKERS):
            out.append(name)
    return out


def iter_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            yield os.path.join(base, fn)


def build_refs(shims):
    mods = [s[:-3] for s in shims]
    refs = {m: set() for m in mods}
    pats = {m: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(m) + r"(?![A-Za-z0-9_])") for m in mods}
    self_path = {m: os.path.join(ROOT, m + ".py") for m in mods}
    for path in iter_files():
        low = path.lower()
        base = os.path.basename(path)
        if not (low.endswith(CODE_EXT) or low.endswith(LAUNCH_EXT) or base.startswith("Dockerfile")):
            continue
        try:
            txt = _read(path)
        except Exception:
            continue
        for m in mods:
            if path == self_path[m]:
                continue  # jangan hitung referensi dari shim itu sendiri
            if pats[m].search(txt):
                refs[m].add(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    return refs


def main():
    list_only = "--list" in sys.argv[1:]
    shims = list_root_shims()
    refs = build_refs(shims)
    dead = [s for s in shims if not refs[s[:-3]]]
    used = [s for s in shims if refs[s[:-3]]]

    if list_only:
        for s in dead:
            print(s)
        return 0

    print("=== Shim root terdeteksi: %d (dipakai=%d, mati=%d) ===" % (len(shims), len(used), len(dead)))
    print("")
    print("--- SHIM MATI (kandidat hapus) ---")
    for s in dead:
        print("  DEAD  %s" % s)
    if not dead:
        print("  (tidak ada - semua shim masih dipakai)")
    print("")
    print("--- SHIM MASIH DIPAKAI (contoh perujuk) ---")
    for s in used:
        ex = sorted(refs[s[:-3]])[:4]
        more = " ..." if len(refs[s[:-3]]) > 4 else ""
        print("  keep  %-30s <- %s%s" % (s, ", ".join(ex), more))
    return 0


if __name__ == "__main__":
    sys.exit(main())
