#!/usr/bin/env bash
# reorg_pkg_as.sh - PR-4+ pemaket cluster generik, VARIAN: nama folder tujuan boleh beda dari prefix file.
# Pindah <prefix>_<mod>.py (root) -> <dest>/<mod>.py via git mv (byte-exact) + shim mundur di root.
# Berguna saat nama paket ideal beda dari prefix file (mis. eval_*.py -> evaluation/).
# Auto-deteksi & patch _BASE_DIR (naik satu level) utk file yg meng-anchor __file__;
# ABORT bila ada __file__ di luar pola dikenal (jangan rusak path diam-diam).
#
# Pakai:  bash scripts/reorg_pkg_as.sh <dest> <prefix> <mod1> <mod2> ...
# Contoh: bash scripts/reorg_pkg_as.sh evaluation eval db sampler harness judge holdout sweep chatbot recall_map routes chatbot_routes
#
# Aman: self-heal CRLF, set autocrlf/fileMode repo-local, guard bersih,
#       trap ERR rollback otomatis, gate py_compile best-effort, commit LOKAL (belum push).

# self-heal CRLF (buang CR lalu jalankan ulang via bash, sekali):
if [ -z "${_DECR:-}" ]; then export _DECR=1; tr -d '\r' < "$0" | bash -s -- "$@"; exit $?; fi
set -eu

# Samakan persepsi git di bash dgn PowerShell (mesin ini punya 2 setup git; repo-local, idempoten).
git config core.autocrlf true  2>/dev/null || true
git config core.fileMode false 2>/dev/null || true

command -v git  >/dev/null || { echo "ERROR: git tak ada di shell ini";  exit 1; }
command -v sed  >/dev/null || { echo "ERROR: sed tak ada di shell ini";  exit 1; }
command -v grep >/dev/null || { echo "ERROR: grep tak ada di shell ini"; exit 1; }

DEST="${1:-}";   shift || true
PREFIX="${1:-}"; shift || true
MODS="$@"
[ -n "$DEST" ]   || { echo "ERROR: dest kosong.  Pakai: bash scripts/reorg_pkg_as.sh <dest> <prefix> <mod1> ..."; exit 1; }
[ -n "$PREFIX" ] || { echo "ERROR: prefix kosong.  Pakai: bash scripts/reorg_pkg_as.sh <dest> <prefix> <mod1> ..."; exit 1; }
[ -n "$MODS" ]   || { echo "ERROR: daftar modul kosong.  Pakai: bash scripts/reorg_pkg_as.sh <dest> <prefix> <mod1> ..."; exit 1; }

# --- Guard (sebelum membuat apa pun) ---
[ -f web_app.py ] || { echo "ERROR: jalankan dari root repo (web_app.py tak ada)"; exit 1; }
[ -d .git ]       || { echo "ERROR: bukan root git repo (.git tak ada)"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: working tree tidak bersih. Bereskan dulu:  bash -c 'git reset --hard HEAD && rm -rf $DEST'"; exit 1
fi
for m in $MODS; do
  [ -f "${PREFIX}_${m}.py" ] || { echo "ERROR: ${PREFIX}_${m}.py tak ada di root"; exit 1; }
done
[ -e "$DEST" ] && { echo "ERROR: '$DEST' sudah ada; batalkan agar tak menimpa."; exit 1; }

# --- Mulai mengubah: pasang jaring rollback otomatis ---
rollback() { echo ""; echo ">> GAGAL -> rollback otomatis ke kondisi bersih."; git reset -q --hard HEAD 2>/dev/null || true; rm -rf "$DEST" 2>/dev/null || true; }
trap 'rollback' ERR

# 1) Buat paket <dest>/
mkdir "$DEST"
cat > "$DEST/__init__.py" <<PY
# -*- coding: utf-8 -*-
"""Paket cluster ${DEST} (PR-4 reorg). Impor lama (import ${PREFIX}_<mod>) didukung sementara lewat shim di root."""
PY

# 2) Pindah byte-exact
for m in $MODS; do
  git mv "${PREFIX}_${m}.py" "$DEST/${m}.py"
done

# 3) Auto-deteksi & patch _BASE_DIR (naik satu level) + jaring pengaman __file__
OLD='_BASE_DIR = os.path.dirname(os.path.abspath(__file__))'
NEWMARK='_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'
for m in $MODS; do
  f="$DEST/${m}.py"
  nmark=$(grep -Fc "$OLD" "$f" || true)
  nfile=$(grep -Fc '__file__' "$f" || true)
  if [ "$nmark" = "1" ] && [ "$nfile" = "1" ]; then
    sed -i 's|_BASE_DIR = os\.path\.dirname(os\.path\.abspath(__file__))|_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))|' "$f"
    grep -Fq "$NEWMARK" "$f" || { echo "ERROR: patch _BASE_DIR di $f gagal."; rollback; trap - ERR; exit 1; }
    echo "PATCH OK: $f (_BASE_DIR naik 1 level)"
  elif [ "$nmark" = "0" ] && [ "$nfile" = "0" ]; then
    echo "SKIP    : $f (tak ada anchor __file__)"
  else
    echo "ERROR: $f memakai __file__ di luar pola yang dikenal (marker=$nmark, __file__=$nfile)."
    echo "       Perlu tinjau manual; dibatalkan agar tak merusak path diam-diam."
    rollback; trap - ERR; exit 1
  fi
done

# 4) Shim mundur di root (alias sys.modules -> objek modul yang sama)
for m in $MODS; do
  cat > "${PREFIX}_${m}.py" <<PY
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-4). Asli dipindah ke ${DEST}/${m}.py.
# import ${PREFIX}_${m} / from ${PREFIX}_${m} import ... tetap jalan sampai pemanggil
# diperbarui ke: from ${DEST} import ${m}  (dibersihkan di PR akhir).
import sys as _sys
import ${DEST}.${m} as _mod
_sys.modules[__name__] = _mod
PY
done

# 5) Stage (scoped) + gerbang py_compile best-effort
STAGE="$DEST"
for m in $MODS; do STAGE="$STAGE ${PREFIX}_${m}.py"; done
git add -A $STAGE
PYBIN=""
for c in python3 python py; do command -v "$c" >/dev/null 2>&1 && { PYBIN="$c"; break; }; done
COMPILE=""
for m in $MODS; do COMPILE="$COMPILE $DEST/${m}.py ${PREFIX}_${m}.py"; done
if [ -n "$PYBIN" ]; then
  echo "Gate: py_compile ($PYBIN)..."
  "$PYBIN" -m py_compile $COMPILE
  echo "GATE OK: py_compile lulus."
else
  echo "CATATAN: 'python' tak ada di shell ini -> py_compile dilewati; WAJIB boot-test dari PowerShell (.venv)."
fi

# 6) Commit LOKAL; lepas jaring rollback
git commit -q -m "refactor(${DEST}): paket ${PREFIX}_* -> ${DEST}/ + shim mundur (PR-4)"
trap - ERR
echo ""
echo "OK: commit PR-4 (${DEST}) dibuat LOKAL (belum di-push)."
echo "LANGKAH BERIKUT (di PowerShell, venv aktif):"
echo "  1) Tutup web_app lama (port 8080), lalu:  python web_app.py"
echo "  2) Boot-test menu/endpoint terkait paket '${DEST}' -> status 200 & data utuh (angka SAMA)."
echo "  3) Bila OK   :  git push"
echo "  4) Bila gagal:  bash -c 'git reset --hard HEAD~1 && rm -rf ${DEST}'"
