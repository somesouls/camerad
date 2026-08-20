# -*- coding: utf-8 -*-
"""Codemod: alihkan semua import nama-flat lama -> jalur paket, lalu shim bisa dihapus.

Peta lama->baru diturunkan OTOMATIS dari tiap shim root (baris `import <dotted> as _mod`).

Mode:
  (default)     ringkasan peta + jumlah (tanpa mengubah berkas)
  --map         cetak peta 'flat -> dotted'
  --list-shims  cetak nama berkas shim (utk git rm)
  --rewrite     tulis-ulang import di semua .py (kecuali berkas shim). Setelah itu
                cek RESIDU; keluar kode !=0 bila masih ada import nama-flat tersisa.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHIM_MARKER = "sys.modules[__name__]"
NEVER = {"web_app.py", "app_core.py"}
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "_legacy"}


def read(p):
    with open(p, "rb") as f:
        return f.read().decode("utf-8", "replace")


def write(p, s):
    with open(p, "wb") as f:
        f.write(s.encode("utf-8"))


def root_shims():
    """map flat_name -> dotted_target"""
    out = {}
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py") or name in NEVER:
            continue
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            continue
        txt = read(p)
        if SHIM_MARKER not in txt:
            continue
        m = re.search(r"^\s*import\s+([A-Za-z_][\w.]*)\s+as\s+_mod\b", txt, re.M)
        if m:
            out[name[:-3]] = m.group(1)
    return out


def py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(base, fn)


def _rewrite_code(code, mapping):
    """rewrite satu 'bagian kode' (tanpa komentar) dari sebuah baris import. return (new, changed)"""
    m = re.match(r"^(\s*)from\s+([A-Za-z_][\w.]*)(\s+import\s+.*)$", code)
    if m:
        ind, mod, rest = m.groups()
        if mod in mapping:
            return "%sfrom %s%s" % (ind, mapping[mod], rest), True
        return code, False
    m = re.match(r"^(\s*)import\s+(.+?)\s*$", code)
    if m:
        ind, body = m.groups()
        parts = [p.strip() for p in body.split(",")]
        newparts = []
        touched = False
        for part in parts:
            mm = re.match(r"^([A-Za-z_][\w.]*)(\s+as\s+([A-Za-z_]\w*))?$", part)
            if mm and mm.group(1) in mapping:
                mod = mm.group(1)
                alias = mm.group(3) or mod  # pertahankan binding nama lama
                newparts.append("%s as %s" % (mapping[mod], alias))
                touched = True
            else:
                newparts.append(part)
        if touched:
            return "%simport %s" % (ind, ", ".join(newparts)), True
        return code, False
    return code, False


def _rewrite_line(line, mapping):
    cr = line.endswith("\r")
    core = line[:-1] if cr else line
    comment = ""
    if "#" in core:
        code, comment = core.split("#", 1)
        comment = "#" + comment
        pad = code[len(code.rstrip()):]
        code = code.rstrip()
    else:
        code, pad = core, ""
    new, changed = _rewrite_code(code, mapping)
    if not changed:
        return line, False
    rebuilt = new + pad + comment + ("\r" if cr else "")
    return rebuilt, True


def _rewrite_dynamic(txt, mapping):
    pat = re.compile(r"(__import__|import_module)\(\s*(['\"])([\w.]+)\2")
    def repl(m):
        name = m.group(3)
        if name in mapping:
            return m.group(0).replace(m.group(2) + name + m.group(2),
                                      m.group(2) + mapping[name] + m.group(2))
        return m.group(0)
    return pat.sub(repl, txt)


def apply_rewrite(mapping):
    shim_paths = {os.path.join(ROOT, f + ".py") for f in mapping}
    files_changed = 0
    for p in py_files():
        if p in shim_paths:
            continue
        txt = read(p)
        lines = txt.split("\n")
        out = []
        changed = False
        for ln in lines:
            nl, ch = _rewrite_line(ln, mapping)
            out.append(nl)
            changed = changed or ch
        newtxt = "\n".join(out)
        dt = _rewrite_dynamic(newtxt, mapping)
        if dt != newtxt:
            changed = True
            newtxt = dt
        if changed:
            write(p, newtxt)
            files_changed += 1
    return files_changed


def residual(mapping):
    flats = set(mapping)
    shim_paths = {os.path.join(ROOT, f + ".py") for f in mapping}
    hits = []
    for p in py_files():
        if p in shim_paths:
            continue
        for i, ln in enumerate(read(p).split("\n"), 1):
            core = ln.rstrip("\r")
            code = core.split("#", 1)[0]
            m = re.match(r"^\s*from\s+([\w.]+)\s+import\s+", code)
            if m and m.group(1) in flats:
                hits.append((p, i, core)); continue
            m2 = re.match(r"^\s*import\s+(.+?)\s*$", code)
            if m2:
                for part in m2.group(1).split(","):
                    tok = (part.strip().split() or [""])[0]
                    if tok in flats:
                        hits.append((p, i, core)); break
            for dm in re.finditer(r"(?:__import__|import_module)\(\s*['\"]([\w.]+)['\"]", code):
                if dm.group(1) in flats:
                    hits.append((p, i, core)); break
    return hits


def main():
    args = sys.argv[1:]
    mapping = root_shims()
    if "--map" in args:
        for k in sorted(mapping):
            print("%-30s -> %s" % (k, mapping[k]))
        return 0
    if "--list-shims" in args:
        for k in sorted(mapping):
            print(k + ".py")
        return 0
    if "--rewrite" in args:
        n = apply_rewrite(mapping)
        print("[migrate] %d shim dipetakan; %d berkas .py ditulis-ulang." % (len(mapping), n))
        hits = residual(mapping)
        if hits:
            print("[migrate] RESIDU import nama-flat masih ada (%d):" % len(hits))
            for p, i, ln in hits[:40]:
                print("   %s:%d  %s" % (os.path.relpath(p, ROOT).replace(os.sep, "/"), i, ln.strip()))
            return 2
        print("[migrate] Residu NOL - semua import sudah pakai jalur paket. Shim aman dihapus.")
        return 0
    print("Shim terdeteksi: %d" % len(mapping))
    for k in sorted(mapping):
        print("  %-30s -> %s" % (k, mapping[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
