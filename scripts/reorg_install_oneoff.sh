#!/usr/bin/env bash
# PR-18: arsipkan install.py (installer sekali-jalan) ke scripts/oneoff/.
# install.py TIDAK di-import modul mana pun (pakai subprocess), patcher fix_*.py
# yang dirujuknya sudah ada di scripts/oneoff/, jadi TIDAK perlu shim root.
# Ops-tool (phaseN_upgrade, phase4_eval, phase5_qa_build) & lib (docstudio,
# llm_fix_final_combined) SENGAJA dibiarkan di root: mereka import modul repo /
# dijalankan dari root, memindah = import patah.
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

# 3) Prasyarat & no-clobber.
if [ ! -f install.py ]; then echo "ABORT: install.py tidak ada di root." >&2; exit 1; fi
if [ ! -d scripts/oneoff ]; then echo "ABORT: scripts/oneoff/ tidak ada." >&2; exit 1; fi
if [ -f scripts/oneoff/install.py ]; then echo "ABORT: scripts/oneoff/install.py sudah ada (jangan timpa)." >&2; exit 1; fi

# 4) GATE awal: py_compile install.py + web_app.
$PY -m py_compile install.py web_app.py
echo "GATE OK: py_compile lulus."

# 5) Pindahkan (git mv agar riwayat terjaga).
git mv install.py scripts/oneoff/install.py

# 6) GATE akhir: py_compile lokasi baru + web_app.
$PY -m py_compile scripts/oneoff/install.py web_app.py

# 7) Commit lokal.
git commit -m "PR-18: arsipkan install.py (installer sekali-jalan) ke scripts/oneoff"

echo ""
echo "OK: commit PR-18 dibuat LOKAL (belum di-push)."
echo "Smoke: $PY -m py_compile scripts/oneoff/install.py"
echo "Boot: $PY web_app.py -> pastikan boot hijau & indeks Q&A tetap 2645 vektor -> git push"
