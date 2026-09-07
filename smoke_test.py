# -*- coding: utf-8 -*-
"""smoke_test.py — Harness uji-asap (smoke test) ringan & offline-safe.

Satu perintah untuk memverifikasi cepat bahwa basis kode sehat SEBELUM
menjalankan / menerapkan perubahan:

    python smoke_test.py

Dua fase:
  [1] COMPILE  — kompilasi SEMUA berkas .py (deteksi SyntaxError dini, mis.
                 akibat file ter-truncate saat push). 100% offline & aman.
  [2] SMOKE    — jalankan smoke '__main__' modul terpilih (offline-safe: tanpa
                 LLM / jaringan / DB, tanpa efek samping berkas) via subprocess;
                 verifikasi exit code 0 dan token penanda muncul di keluaran.

Keluar dengan kode 0 bila semua lulus, selain itu 1 (ramah CI / pra-deploy).

Sifat: ADITIF. Tidak mengubah modul mana pun; hanya menambah alat bantu.
"""
import os
import re
import sys
import subprocess
import compileall

ROOT = os.path.dirname(os.path.abspath(__file__))

# Direktori yang dilewati saat kompilasi (lingkungan virtual & artefak).
_SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules",
              ".mypy_cache", ".pytest_cache", "build", "dist"}

# Target smoke '__main__' yang OFFLINE-SAFE (tanpa LLM / jaringan / DB, tanpa
# efek samping berkas). Tambah entri baru HANYA bila modulnya benar-benar
# offline-safe; bila smoke-nya menyentuh DB, arahkan override env DB ke path
# sementara sebelum menambahkannya di sini.
#   (nama_modul, token_penanda)
_SMOKE_TARGETS = [
    ("knowledge.agentic", "AGENTIC_SMOKE_OK"),
    ("rag.kb_search", "KB_SEARCH_SMOKE_OK"),
]


def _skip_regex():
    parts = [re.escape(d) for d in sorted(_SKIP_DIRS)]
    return re.compile(r"[\\/](%s)([\\/]|$)" % "|".join(parts))


def _compile_all():
    print("[1/2] COMPILE — kompilasi semua berkas .py ...")
    try:
        ok = compileall.compile_dir(ROOT, quiet=1, rx=_skip_regex(), workers=0)
    except Exception as e:  # pragma: no cover - jaga-jaga
        print("      -> GAGAL: %s" % e)
        return False
    print("      -> %s" % ("OK" if ok else "GAGAL (ada SyntaxError)"))
    return bool(ok)


def _run_smoke(module, token, timeout=90):
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        p = subprocess.run(
            [sys.executable, "-m", module],
            cwd=ROOT, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return False, "timeout > %ss" % timeout
    except Exception as e:  # pragma: no cover - jaga-jaga
        return False, str(e)
    out = (p.stdout or b"").decode("utf-8", "replace")
    tail = out.strip()[-800:]
    if p.returncode != 0:
        return False, "exit=%d\n%s" % (p.returncode, tail)
    if token and token not in out:
        return False, "token '%s' tak ditemukan\n%s" % (token, tail)
    return True, token or "OK"


def main():
    print("=" * 60)
    print("SMOKE TEST — Camerad")
    print("=" * 60)
    results = []

    compiled = _compile_all()
    results.append(("compile", compiled))

    print("[2/2] SMOKE — jalankan smoke modul offline-safe ...")
    for module, token in _SMOKE_TARGETS:
        ok, detail = _run_smoke(module, token)
        print("      - %-24s %s" % (module, "OK" if ok else "GAGAL"))
        if not ok:
            print("        " + detail.replace("\n", "\n        "))
        results.append((module, ok))

    print("-" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("HASIL: %d/%d lulus" % (passed, total))
    all_ok = (passed == total)
    print("STATUS: %s" % ("SMOKE_OK" if all_ok else "SMOKE_FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
