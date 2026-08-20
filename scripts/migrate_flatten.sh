#!/usr/bin/env bash
# PR-21: migrasi SEKALI-JALAN. Alihkan semua import nama-flat -> jalur paket, lalu
# hapus semua shim root. Ter-gate (import web_app) + rollback otomatis bila gagal.
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
echo "Python runner: $PY"

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

echo "== 1/4 tulis-ulang import =="
if ! $PY scripts/oneoff/migrate_flatten.py --rewrite; then
  echo "GAGAL: rewrite/residu."; rollback; exit 2
fi

echo "== 2/4 GATE import web_app (pra-hapus) =="
if ! $PY -c "import web_app"; then echo "GATE pra gagal."; rollback; exit 3; fi

echo "== 3/4 hapus semua shim =="
SHIMS="$($PY scripts/oneoff/migrate_flatten.py --list-shims)"
N=0
if [ -n "$SHIMS" ]; then
  git rm --quiet -- $SHIMS
  N=$(printf '%s\n' "$SHIMS" | grep -c '\.py$' || true)
fi
echo "  $N shim di-git rm."

echo "== 4/4 GATE import web_app (pasca-hapus) + py_compile web_app.py =="
if ! $PY -c "import web_app"; then echo "GATE pasca gagal."; rollback; exit 4; fi
if ! $PY -m py_compile web_app.py; then echo "py_compile gagal."; rollback; exit 5; fi

git add -A
git commit --quiet -m "PR-21: alihkan semua import ke jalur paket + hapus ${N} shim root (root bersih)"
echo ""
echo "OK: commit PR-21 dibuat LOKAL (belum di-push). Shim dihapus: ${N}."
echo "Boot: $PY web_app.py -> pastikan boot hijau & 'indeks Q&A: 2645 vektor' -> git push"
