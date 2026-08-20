#!/usr/bin/env bash
# reorg_pkg_soft.sh - varian LUNAK reorg_pkg.sh (PR-4+).
# Sama seperti reorg_pkg.sh (git mv <pkg>_<mod>.py -> <pkg>/<mod>.py + shim mundur,
# auto-patch _BASE_DIR naik 1 level), TAPI bila file memakai __file__ di luar pola
# _BASE_DIR baku (mis. modul avaya_pipeline yg pakai _HERE + os.getcwd() fallback),
# script TIDAK meng-ABORT: file tetap dipindah+shim TANPA patch, disertai WARN.
# GUNAKAN HANYA untuk modul yang path-nya SUDAH DIAUDIT aman (fallback CWD/embedded),
# lalu WAJIB boot-test. Untuk pola _BASE_DIR ganda/ambigu tetap ABORT.
#
# Pakai:  bash scripts/reorg_pkg_soft.sh <pkg> <mod1> <mod2> ...
# Contoh: bash scripts/reorg_pkg_soft.sh avaya client db pipeline
#
# Aman: self-heal CRLF, autocrlf/fileMode repo-local, guard bersih, trap rollback,
#       gate py_compile best-effort, commit LOKAL (belum push).

if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi
set -eu

git config core.autocrlf true  2>/dev/null || true
git config core.fileMode false 2>/dev/null || true

command -v git  >/dev/null || { echo "ERROR: git tak ada di shell ini";  exit 1; }
command -v sed  >/dev/null || { echo "ERROR: sed tak ada di shell ini";  exit 1; }
command -v grep >/dev/null || { echo "ERROR: grep tak ada di shell ini"; exit 1; }

PKG="${1:-}"
shift || true
MODS="$@"
[ -n "$PKG" ]  || { echo "ERROR: pkg kosong.  Pakai: bash scripts/reorg_pkg_soft.sh <pkg> <mod1> <mod2> ..."; exit 1; }
[ -n "$MODS" ] || { echo "ERROR: daftar modul kosong.  Pakai: bash scripts/reorg_pkg_soft.sh <pkg> <mod1> ..."; exit 1; }

[ -f web_app.py ] || { echo "ERROR: jalankan dari root repo (web_app.py tak ada)"; exit 1; }
[ -d .git ]       || { echo "ERROR: bukan root git repo (.git tak ada)"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree tidak bersih. Bereskan dulu:  bash -c 'git reset --hard HEAD && rm -rf $PKG'"; exit 1
fi
for m in $MODS; do
  [ -f "${PKG}_${m}.py" ] || { echo "ERROR: ${PKG}_${m}.py tak ada di root"; exit 1; }
done
[ -e "$PKG" ] && { echo "ERROR: '$PKG' sudah ada; batalkan agar tak menimpa."; exit 1; }

rollback() { echo ""; echo ">> GAGAL -> rollback otomatis ke kondisi bersih."; git reset -q --hard HEAD 2>/dev/null || true; rm -rf "$PKG" 2>/dev/null || true; }
trap 'rollback' ERR

mkdir "$PKG"
cat > "$PKG/__init__.py" <<PY
# -*- coding: utf-8 -*-
"""Paket cluster ${PKG} (PR-4 reorg). Impor lama (import ${PKG}_<mod>) didukung sementara lewat shim di root."""
PY

for m in $MODS; do
  git mv "${PKG}_${m}.py" "$PKG/${m}.py"
done

OLD='_BASE_DIR = os.path.dirname(os.path.abspath(__file__))'
NEWMARK='_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'
for m in $MODS; do
  f="$PKG/${m}.py"
  nmark=$(grep -Fc "$OLD" "$f" || true)
  nfile=$(grep -Fc '__file__' "$f" || true)
  if [ "$nmark" = "1" ] && [ "$nfile" = "1" ]; then
    sed -i 's|_BASE_DIR = os\.path\.dirname(os\.path\.abspath(__file__))|_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))|' "$f"
    grep -Fq "$NEWMARK" "$f" || { echo "ERROR: patch _BASE_DIR di $f gagal."; rollback; trap - ERR; exit 1; }
    echo "PATCH OK: $f (_BASE_DIR naik 1 level)"
  elif [ "$nfile" = "0" ]; then
    echo "SKIP    : $f (tak ada anchor __file__)"
  elif [ "$nmark" = "0" ]; then
    echo "WARN    : $f pakai __file__ NON-STANDAR (__file__=$nfile, bukan _BASE_DIR) -> dipindah TANPA patch."
    echo "          WAJIB boot-test: pastikan path berbasis __file__ tetap benar (harus punya fallback CWD/embedded)."
  else
    echo "ERROR: $f memakai _BASE_DIR/__file__ di luar pola dikenal (marker=$nmark, __file__=$nfile)."
    echo "       Perlu tinjau manual; dibatalkan agar tak merusak path diam-diam."
    rollback; trap - ERR; exit 1
  fi
done

for m in $MODS; do
  cat > "${PKG}_${m}.py" <<PY
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke ${PKG}/${m}.py.
# import ${PKG}_${m} / from ${PKG}_${m} import ... tetap jalan sampai pemanggil
# diperbarui ke: from ${PKG} import ${m}  (dibersihkan di PR akhir).
import sys as _sys
import ${PKG}.${m} as _mod
_sys.modules[__name__] = _mod
PY
done

STAGE="$PKG"
for m in $MODS; do STAGE="$STAGE ${PKG}_${m}.py"; done
git add -A $STAGE
PYBIN=""
for c in python3 python py; do command -v "$c" >/dev/null 2>&1 && { PYBIN="$c"; break; }; done
COMPILE=""
for m in $MODS; do COMPILE="$COMPILE $PKG/${m}.py ${PKG}_${m}.py"; done
if [ -n "$PYBIN" ]; then
  echo "Gate: py_compile ($PYBIN)..."
  "$PYBIN" -m py_compile $COMPILE
  echo "GATE OK: py_compile lulus."
else
  echo "CATATAN: 'python' tak ada di shell ini -> py_compile dilewati; WAJIB boot-test dari PowerShell (.venv)."
fi

git commit -q -m "refactor(${PKG}): paket ${PKG}_* -> ${PKG}/ + shim; __file__ non-standar dibiarkan (PR-4 soft)"
trap - ERR
echo ""
echo "OK: commit PR-4 soft (${PKG}) dibuat LOKAL (belum di-push)."
echo "LANGKAH BERIKUT (di PowerShell, venv aktif):"
echo "  1) Tutup web_app lama (port 8080), lalu:  python web_app.py"
echo "  2) Boot-test menu/endpoint paket '${PKG}' -> 200 & data utuh (angka SAMA)."
echo "  3) Bila OK   :  git push"
echo "  4) Bila gagal:  bash -c 'git reset --hard HEAD~1 && rm -rf ${PKG}'"
