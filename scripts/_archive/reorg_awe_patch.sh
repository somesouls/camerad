#!/usr/bin/env bash
# PR-16: pindahkan awe_botfilter_patch.py -> awe/botfilter_patch.py, root jadi shim.
# Nol rewrite: modul hanya import re/json/rag_engine (tak ada __file__).
# rag_sources_patch masih `import awe_botfilter_patch`, jadi shim root WAJIB.
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
if [ ! -f awe_botfilter_patch.py ]; then
  echo "ABORT: awe_botfilter_patch.py tidak ada di root." >&2; exit 1
fi
if [ ! -d awe ]; then
  echo "ABORT: paket awe/ tidak ada." >&2; exit 1
fi
if [ -f awe/botfilter_patch.py ]; then
  echo "ABORT: awe/botfilter_patch.py sudah ada (jangan timpa)." >&2; exit 1
fi
[ -f awe/__init__.py ] || { echo "ABORT: awe/__init__.py hilang." >&2; exit 1; }

# 4) GATE awal: py_compile file sumber + web_app.
$PY -m py_compile awe_botfilter_patch.py web_app.py
echo "GATE OK: py_compile lulus."

# 5) Pindahkan isi asli -> paket, lalu tulis shim di root.
cp awe_botfilter_patch.py awe/botfilter_patch.py

cat > awe_botfilter_patch.py <<'PYEOF'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-16). Asli dipindah ke awe/botfilter_patch.py
import sys as _sys
import awe.botfilter_patch as _mod
_sys.modules[__name__] = _mod
PYEOF

# 6) GATE akhir: py_compile modul baru + shim + web_app.
$PY -m py_compile awe/botfilter_patch.py awe_botfilter_patch.py web_app.py

# 7) Commit lokal (path eksplisit).
git add awe/botfilter_patch.py awe_botfilter_patch.py
git commit -m "PR-16: kelompokkan patch AWE ke paket awe (botfilter_patch)"

echo ""
echo "OK: commit PR-16 awe botfilter patch dibuat LOKAL (belum di-push)."
echo "Smoke: $PY -c \"import awe.botfilter_patch; import awe_botfilter_patch; print('AWE PATCH OK')\""
echo "Boot: $PY web_app.py -> pastikan '[awe_botfilter_patch] retrieval AWE kini membuang Bot/CCAI' muncul & indeks Q&A tetap 2645 vektor -> git push"
