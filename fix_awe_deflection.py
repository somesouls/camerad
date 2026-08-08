#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_awe_deflection.py — sambungkan fitur "Deflection Gap → Materi" (KPI #1).

Menyuntikkan 2 perubahan kecil ke berkas yang sudah ada (idempoten & non-fatal):
  1. awe_routes.py    : halaman /awe/deflection-gap dari REDIRECT -> render
                        template awe_deflection.html (active_page 'awe_deflgap').
  2. awe_analytics.py : pasang hook API awe_deflection.register(app) setelah
                        awe_assess (agar tidak perlu menyentuh web_app.py).

Berkas modul (awe_deflection.py) & template (templates/awe_deflection.html)
dikirim lewat git pull; skrip ini hanya menyambungkan rute.

Jalankan dari root proyek:  python fix_awe_deflection.py
"""
import io
import os
import sys
import py_compile


def _read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _compile_ok(path):
    try:
        py_compile.compile(path, doraise=True)
        return True
    except py_compile.PyCompileError as e:
        print("    [ERROR] py_compile gagal: %s" % e)
        return False


def patch_routes(path="awe_routes.py"):
    if not os.path.exists(path):
        print("[lewati] %s tidak ditemukan." % path)
        return True
    src = _read(path)
    if 'awe_deflection.html' in src:
        print("[skip] %s sudah render halaman deflection." % path)
        return True
    old = (
        'async def awe_deflection_gap_page(request: Request):\n'
        '    return RedirectResponse("/awe/coverage", status_code=302)'
    )
    new = (
        'async def awe_deflection_gap_page(request: Request):\n'
        '    return render_page(request, "awe_deflection.html", "awe_deflgap")'
    )
    n = src.count(old)
    if n != 1:
        print("[warn] %s: anchor redirect ditemukan %d kali (harus 1). "
              "Lewati tanpa mengubah." % (path, n))
        return True  # non-fatal
    _write(path, src.replace(old, new, 1))
    if not _compile_ok(path):
        return False
    print("[ok] %s: /awe/deflection-gap kini merender awe_deflection.html." % path)
    return True


def patch_analytics(path="awe_analytics.py"):
    if not os.path.exists(path):
        print("[lewati] %s tidak ditemukan." % path)
        return True
    src = _read(path)
    if 'import awe_deflection' in src:
        print("[skip] %s sudah memasang hook awe_deflection." % path)
        return True
    anchor = (
        '    try:\n'
        '        import awe_assess\n'
        '        awe_assess.register(app)\n'
        '    except Exception:\n'
        '        import traceback\n'
        '        traceback.print_exc()'
    )
    addition = (
        '\n\n'
        '    # KPI: Deflection Gap -> Materi (pengetahuan bot). Pasang API di sini\n'
        '    # agar tidak perlu menyentuh web_app.py/studio_routes.py. Halaman\n'
        '    # /awe/deflection-gap dirender awe_routes.py (awe_deflection.html).\n'
        '    try:\n'
        '        import awe_deflection\n'
        '        awe_deflection.register(app)\n'
        '    except Exception:\n'
        '        import traceback\n'
        '        traceback.print_exc()'
    )
    n = src.count(anchor)
    if n != 1:
        print("[warn] %s: anchor awe_assess ditemukan %d kali (harus 1). "
              "Lewati tanpa mengubah." % (path, n))
        return True  # non-fatal
    _write(path, src.replace(anchor, anchor + addition, 1))
    if not _compile_ok(path):
        return False
    print("[ok] %s: hook awe_deflection.register(app) terpasang." % path)
    return True


def main():
    print("== fix_awe_deflection: sambungkan Deflection Gap -> Materi ==")
    ok = True
    ok = patch_routes() and ok
    ok = patch_analytics() and ok
    if os.path.exists("awe_deflection.py"):
        _compile_ok("awe_deflection.py")
    else:
        print("[info] awe_deflection.py belum ada di folder — pastikan sudah "
              "'git pull' sebelum menjalankan skrip ini.")
    print("== selesai (%s) ==" % ("OK" if ok else "ADA GALAT"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
