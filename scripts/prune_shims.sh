#!/usr/bin/env bash
# PR-20: laporkan / hapus shim kompatibilitas root yang sudah tidak dipakai.
#   bash scripts/prune_shims.sh           -> LAPORAN saja (read-only)
#   bash scripts/prune_shims.sh --apply   -> git rm shim mati + GATE import web_app
#                                            (auto-restore bila GATE gagal), lalu commit lokal.
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

MODE="${1:-report}"

if [ "$MODE" != "--apply" ]; then
  echo "=== MODE LAPORAN (read-only) ==="
  $PY scripts/oneoff/prune_shims.py
  echo ""
  echo "Jika daftar DEAD sudah benar, jalankan: bash scripts/prune_shims.sh --apply"
  exit 0
fi

# ---- APPLY ----
if [ -n "$(git status --porcelain)" ]; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."; exit 1
fi

mapfile -t DEAD < <($PY scripts/oneoff/prune_shims.py --list)
if [ "${#DEAD[@]}" -eq 0 ]; then
  echo "Tidak ada shim mati. Tidak ada yang dihapus."; exit 0
fi
echo "Shim mati yang akan dihapus (${#DEAD[@]}):"
printf '  - %s\n' "${DEAD[@]}"

git rm --quiet -- "${DEAD[@]}"

echo "GATE: import web_app ..."
if ! $PY -c "import web_app"; then
  echo "GATE GAGAL -> pulihkan semua shim (git checkout HEAD)."
  git checkout HEAD -- "${DEAD[@]}"
  exit 3
fi
echo "GATE OK: import web_app lulus."

$PY -m py_compile web_app.py
echo "GATE OK: py_compile web_app.py lulus."

git commit --quiet -m "PR-20: hapus ${#DEAD[@]} shim kompatibilitas root yang sudah tidak dipakai"
echo ""
echo "OK: commit PR-20 dibuat LOKAL (belum di-push)."
echo "Boot: $PY web_app.py -> pastikan boot hijau & indeks Q&A tetap 2645 vektor -> git push"
