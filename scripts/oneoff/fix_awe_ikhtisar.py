#!/usr/bin/env python3
"""Patcher idempoten untuk memasang Ikhtisar AWE (§13.3).

Jalankan dari root proyek (folder yang berisi awe_analytics.py & templates/):
    python fix_awe_ikhtisar.py

Melakukan dua hal (aman diulang):
  1. awe_analytics.py: sisipkan hook `import awe_overview; awe_overview.register(
     app, render_page=render_page)` di dalam register(), setelah blok awe_assess.
  2. templates/base.html: tambah item sidebar "Ikhtisar AWE" di grup AWE.

Modul awe_overview.py & templates/awe_ikhtisar.html harus sudah ada (di-pull
lebih dulu). Skrip ini TIDAK fatal: mencatat peringatan lalu keluar 0 agar
aman dipakai dalam rangkaian installer.
"""

import io
import os
import sys
import py_compile

WARN = []


def _read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def patch_analytics(root):
    path = os.path.join(root, "awe_analytics.py")
    if not os.path.exists(path):
        WARN.append("awe_analytics.py tidak ditemukan; lewati hook.")
        return
    src = _read(path)
    if "import awe_overview" in src:
        print("[skip] hook awe_overview sudah ada di awe_analytics.py")
        return
    anchor = (
        "    try:\n"
        "        import awe_assess\n"
        "        awe_assess.register(app)\n"
        "    except Exception:\n"
        "        import traceback\n"
        "        traceback.print_exc()\n"
    )
    n = src.count(anchor)
    if n != 1:
        WARN.append(
            "anchor blok awe_assess ditemukan %d kali (harusnya 1); "
            "hook awe_overview dilewati." % n
        )
        return
    inject = anchor + (
        "\n"
        "    # Ikhtisar AWE (KPI ringkas §13.3). Dipasang di sini karena\n"
        "    # register() ini memegang render_page dan menjadi hub sub-menu AWE.\n"
        "    try:\n"
        "        import awe_overview\n"
        "        awe_overview.register(app, render_page=render_page)\n"
        "    except Exception:\n"
        "        import traceback\n"
        "        traceback.print_exc()\n"
    )
    src = src.replace(anchor, inject, 1)
    _write(path, src)
    try:
        py_compile.compile(path, doraise=True)
        print("[ok] hook awe_overview ditambahkan ke awe_analytics.py (compile OK)")
    except py_compile.PyCompileError as ex:
        WARN.append("awe_analytics.py gagal compile setelah patch: %s" % ex)


def patch_base(root):
    path = os.path.join(root, "templates", "base.html")
    if not os.path.exists(path):
        WARN.append("templates/base.html tidak ditemukan; lewati nav.")
        return
    src = _read(path)
    if "awe_ikhtisar" in src:
        print("[skip] item sidebar Ikhtisar AWE sudah ada di base.html")
        return
    anchor = (
        '        {% if can_awe %}\n'
        '        <div class="sec-label" style="margin-top:14px;">AWE (Avaya)</div>\n'
    )
    n = src.count(anchor)
    if n != 1:
        WARN.append(
            "anchor label grup AWE ditemukan %d kali (harusnya 1); "
            "item sidebar dilewati." % n
        )
        return
    item = (
        '        <a class="tool-side{% if active_page == \'awe_ikhtisar\' %} active{% endif %}" href="/awe/ikhtisar">\n'
        '          <div class="ic c-blue"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg></div>\n'
        '          <b>Ikhtisar AWE</b>\n'
        '        </a>\n'
    )
    src = src.replace(anchor, anchor + item, 1)
    _write(path, src)
    print("[ok] item sidebar Ikhtisar AWE ditambahkan ke base.html")


def main():
    root = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.getcwd()
    print("Root proyek:", root)
    if not os.path.exists(os.path.join(root, "awe_overview.py")):
        WARN.append(
            "awe_overview.py belum ada di root — pastikan sudah 'git pull'."
        )
    if not os.path.exists(os.path.join(root, "templates", "awe_ikhtisar.html")):
        WARN.append("templates/awe_ikhtisar.html belum ada — pastikan sudah 'git pull'.")
    patch_analytics(root)
    patch_base(root)
    if WARN:
        print("\n=== PERINGATAN (non-fatal) ===")
        for w in WARN:
            print(" - " + w)
        print("Perbaiki bila perlu, lalu jalankan ulang (aman/idempoten).")
    else:
        print("\nSelesai. Ikhtisar AWE siap. Restart UI lalu buka /awe/ikhtisar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
