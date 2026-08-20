#!/usr/bin/env bash
# PR-17: pindahkan step9_patch.py & step10_patch.py -> pipeline/, root jadi shim.
# Nol rewrite: keduanya hanya import os/re/json + pipeline_routes (shim), tanpa __file__.
# web_app.py masih `import step9_patch` / `import step10_patch`, jadi shim root WAJIB.
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

MODS="step9_patch step10_patch"

# 3) Prasyarat & no-clobber.
if [ ! -d pipeline ]; then echo "ABORT: paket pipeline/ tidak ada." >&2; exit 1; fi
[ -f pipeline/__init__.py ] || { echo "ABORT: pipeline/__init__.py hilang." >&2; exit 1; }
for m in $MODS; do
  if [ ! -f "$m.py" ]; then echo "ABORT: $m.py tidak ada di root." >&2; exit 1; fi
  if [ -f "pipeline/$m.py" ]; then echo "ABORT: pipeline/$m.py sudah ada (jangan timpa)." >&2; exit 1; fi
done

# 4) GATE awal: py_compile sumber + web_app.
$PY -m py_compile step9_patch.py step10_patch.py web_app.py
echo "GATE OK: py_compile lulus."

# 5) Pindahkan isi asli -> paket, lalu tulis shim di root.
for m in $MODS; do
  cp "$m.py" "pipeline/$m.py"
  cat > "$m.py" <<PYEOF
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-17). Asli dipindah ke pipeline/$m.py
import sys as _sys
import pipeline.$m as _mod
_sys.modules[__name__] = _mod
PYEOF
done

# 6) GATE akhir: py_compile modul baru + shim + web_app.
$PY -m py_compile pipeline/step9_patch.py pipeline/step10_patch.py step9_patch.py step10_patch.py web_app.py

# 7) Commit lokal (path eksplisit).
git add pipeline/step9_patch.py pipeline/step10_patch.py step9_patch.py step10_patch.py
git commit -m "PR-17: kelompokkan patch step9/step10 ke paket pipeline (step9_patch, step10_patch)"

echo ""
echo "OK: commit PR-17 step patches dibuat LOKAL (belum di-push)."
echo "Smoke: $PY -c \"import pipeline.step9_patch, pipeline.step10_patch; import step9_patch, step10_patch; print('STEP PATCH OK')\""
echo "Boot: $PY web_app.py -> pastikan '[step9_patch] ...' & '[step10_patch] ...' muncul & indeks Q&A tetap 2645 vektor -> git push"
