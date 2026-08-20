#!/usr/bin/env bash
# PR-22: migrasi SEKALI-JALAN. Alihkan semua import nama-flat -> jalur paket, lalu
# hapus semua shim root. GATE = py_compile (interpreter-agnostic) + verifikasi target
# + cek residu; rollback otomatis (git reset --hard) bila gagal.
#   bash scripts/migrate_flatten.sh           -> PREVIEW peta (read-only)
#   bash scripts/migrate_flatten.sh --apply   -> eksekusi penuh (commit lokal, belum push)
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"

PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
[ -n "$PY" ] || { echo "ABORT: python tak ditemukan."; exit 1; }
echo "Python runner (codemod/py_compile): $PY"

MODE="${1:-preview}"

if [ "$MODE" != "--apply" ]; then
  echo "=== PREVIEW peta lama->baru (tanpa mengubah apa pun) ==="
  $PY scripts/oneoff/migrate_flatten.py --map
  echo ""
  echo "Untuk eksekusi penuh: bash scripts/migrate_flatten.sh --apply"
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."; exit 1
fi
BASE="$(git rev-parse --short HEAD)"
rollback() { echo "ROLLBACK -> git reset --hard $BASE"; git reset --hard "$BASE" >/dev/null 2>&1 || true; }

echo "== 1/5 tulis-ulang import (+ cek residu) =="
$PY scripts/oneoff/migrate_flatten.py --rewrite || { echo "GAGAL: rewrite/residu."; rollback; exit 2; }

echo "== 2/5 verifikasi semua target paket ada =="
$PY scripts/oneoff/migrate_flatten.py --verify-targets || { echo "GAGAL: target paket hilang."; rollback; exit 3; }

echo "== 3/5 GATE py_compile semua .py (pra-hapus) =="
$PY scripts/oneoff/migrate_flatten.py --compile || { echo "GAGAL: py_compile pra."; rollback; exit 4; }

echo "== 4/5 hapus semua shim =="
SHIMS="$($PY scripts/oneoff/migrate_flatten.py --list-shims)"
N=0
if [ -n "$SHIMS" ]; then
  git rm --quiet -- $SHIMS
  N=$(printf '%s\n' "$SHIMS" | grep -c '\.py$' || true)
fi
echo "  $N shim di-git rm."

echo "== 5/5 GATE py_compile semua .py (pasca-hapus) =="
$PY scripts/oneoff/migrate_flatten.py --compile || { echo "GAGAL: py_compile pasca."; rollback; exit 5; }

git add -u
git commit --quiet -m "PR-22: alihkan semua import ke jalur paket + hapus ${N} shim root (root bersih)"
echo ""
echo "OK: commit LOKAL dibuat (belum push). Shim dihapus: ${N}. HEAD-sebelumnya: ${BASE}"
echo ""
echo "PENTING: gate di skrip ini hanya py_compile (bash/WSL tak punya deps app)."
echo "Verifikasi RUNTIME sekarang pakai venv Windows kamu:"
echo "   python web_app.py    # pastikan boot hijau & 'indeks Q&A: 2645 vektor'"
echo "Kalau boot HIJAU  -> git push"
echo "Kalau boot GAGAL  -> git reset --hard ${BASE}   (batalkan migrasi, balik bersih)"
