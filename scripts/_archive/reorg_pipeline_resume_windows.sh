#!/usr/bin/env bash
set -euo pipefail

# Resume/finalize PR-5 pipeline reorg after the first script stopped on Windows
# Git Bash because `python` was not found. This script is idempotent enough for
# the observed partial state: pipeline/* may already contain copied modules,
# while root pipeline_*.py files may still be originals.

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then
      PY="py -3"
    else
      PY="$c"
    fi
    break
  fi
done
if [ -z "$PY" ]; then
  echo "ABORT: Python tidak ditemukan di Git Bash. Jalankan dari PowerShell:" >&2
  echo "  py -3 scripts\\finish_pipeline_reorg.py" >&2
  exit 1
fi

echo "Python runner: $PY"

# If the previous script did not reach the copy stage for some reason, copy now.
# If it already copied, this is harmless because roots are still originals until
# shims are written below.
mkdir -p pipeline
cp pipeline_routes.py  /tmp/pipeline_routes.current.py
cp pipeline_helpers.py /tmp/pipeline_helpers.current.py
cp pipeline_steps.py   /tmp/pipeline_steps.current.py
cp pipeline_store.py   /tmp/pipeline_store.current.py

# Detect whether root files are already shims. If not, source current roots into
# package files. If they are already shims, keep package files as-is.
if ! grep -q "Asli dipindah ke pipeline/routes.py" pipeline_routes.py 2>/dev/null; then
  cp /tmp/pipeline_routes.current.py pipeline/routes.py
fi
if ! grep -q "Asli dipindah ke pipeline/helpers.py" pipeline_helpers.py 2>/dev/null; then
  cp /tmp/pipeline_helpers.current.py pipeline/helpers.py
fi
if ! grep -q "Asli dipindah ke pipeline/steps.py" pipeline_steps.py 2>/dev/null; then
  cp /tmp/pipeline_steps.current.py pipeline/steps.py
fi
if ! grep -q "Asli dipindah ke pipeline/store.py" pipeline_store.py 2>/dev/null; then
  cp /tmp/pipeline_store.current.py pipeline/store.py
fi

$PY - <<'PY'
from pathlib import Path
# Patch _BASE_DIR in moved store so pipeline_store.db remains at repo root.
p = Path('pipeline/store.py')
s = p.read_text(encoding='utf-8')
old = '_BASE_DIR = os.path.dirname(os.path.abspath(__file__))'
new = '_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'
if new not in s:
    if old not in s:
        raise SystemExit('ABORT: anchor _BASE_DIR standar tidak ditemukan di pipeline/store.py')
    s = s.replace(old, new, 1)
    p.write_text(s, encoding='utf-8')
    print('PATCH OK: pipeline/store.py (_BASE_DIR naik 1 level)')
else:
    print('SKIP PATCH: pipeline/store.py sudah naik 1 level')

# Convert moved modules to package-local imports.
repls = {
    Path('pipeline/routes.py'): {
        'import pipeline_store as pstore': 'from pipeline import store as pstore',
        'from pipeline_helpers import (': 'from pipeline.helpers import (',
        'from pipeline_steps import *': 'from pipeline.steps import *',
        'from pipeline_steps import (': 'from pipeline.steps import (',
    },
    Path('pipeline/helpers.py'): {
        'import pipeline_store as pstore': 'from pipeline import store as pstore',
    },
    Path('pipeline/steps.py'): {
        'from pipeline_helpers import (': 'from pipeline.helpers import (',
    },
}
for p, mp in repls.items():
    s = p.read_text(encoding='utf-8')
    orig = s
    for old, new in mp.items():
        s = s.replace(old, new)
    if s != orig:
        p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: pipeline/* memakai package-local imports')
PY

cat > pipeline_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/routes.py.
# import pipeline_routes / from pipeline_routes import ... tetap jalan sampai
# pemanggil diperbarui ke: from pipeline import routes.
import sys as _sys
import pipeline.routes as _mod
_sys.modules[__name__] = _mod
PY

cat > pipeline_helpers.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/helpers.py.
import sys as _sys
import pipeline.helpers as _mod
_sys.modules[__name__] = _mod
PY

cat > pipeline_steps.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/steps.py.
import sys as _sys
import pipeline.steps as _mod
_sys.modules[__name__] = _mod
PY

cat > pipeline_store.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-5). Asli dipindah ke pipeline/store.py.
import sys as _sys
import pipeline.store as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  pipeline/__init__.py pipeline/routes.py pipeline/helpers.py pipeline/steps.py pipeline/store.py \
  pipeline_routes.py pipeline_helpers.py pipeline_steps.py pipeline_store.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add pipeline/__init__.py pipeline/routes.py pipeline/helpers.py pipeline/steps.py pipeline/store.py \
        pipeline_routes.py pipeline_helpers.py pipeline_steps.py pipeline_store.py

git commit -m "PR-5: pindahkan modul pipeline ke paket pipeline"

echo
echo "OK: commit PR-5 reorg pipeline dibuat LOKAL (belum di-push)."
echo "Berikutnya: python web_app.py -> kalau hijau: git push"
