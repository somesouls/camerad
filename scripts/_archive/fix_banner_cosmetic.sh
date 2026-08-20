#!/usr/bin/env bash
# PR-19 (kosmetik): jalankan patcher banner web_app.py + GATE py_compile + commit.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# 1) Working tree harus bersih.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu." >&2
  exit 1
fi

# 2) Autodetect runner python.
PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
if [ -z "$PY" ]; then echo "ABORT: python tidak ditemukan." >&2; exit 1; fi
echo "Python runner: $PY"

# 3) Prasyarat.
if [ ! -f web_app.py ]; then echo "ABORT: web_app.py tidak ada di root." >&2; exit 1; fi
if [ ! -f scripts/oneoff/fix_banner_cosmetic.py ]; then echo "ABORT: patcher tidak ada." >&2; exit 1; fi

# 4) GATE awal.
$PY -m py_compile web_app.py
echo "GATE OK (pra): py_compile lulus."

# 5) Terapkan patch banner.
$PY scripts/oneoff/fix_banner_cosmetic.py

# 6) GATE akhir.
$PY -m py_compile web_app.py
echo "GATE OK (pasca): py_compile lulus."

# 7) Commit (kalau ada perubahan).
if git diff --quiet -- web_app.py; then
  echo "Tidak ada perubahan pada web_app.py (mungkin sudah diterapkan). Batal commit."
  exit 0
fi
git add web_app.py
git commit -m "PR-19: banner web_app.py tampilkan IP LAN nyata + hapus baris backend :8000 yang menyesatkan"

echo ""
echo "OK: commit PR-19 dibuat LOKAL (belum di-push)."
echo "Boot: $PY web_app.py -> cek banner 'Dari PC lain LAN: http://<IP asli>:8080/' & indeks Q&A tetap 2645 vektor -> git push"
