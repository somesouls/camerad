#!/usr/bin/env bash
set -euo pipefail

# Reorg pipeline_* root modules into pipeline/ after package scaffold exists.
# Safe behavior:
# - backup originals to temp
# - overwrite scaffold files with byte-exact original content
# - patch pipeline/store.py _BASE_DIR one level up so pipeline_store.db remains at repo root
# - replace root files with backward-compatible shims
# - py_compile gate
# - commit local only; user should boot-test then git push

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu." >&2
  exit 1
fi

for f in pipeline_routes.py pipeline_helpers.py pipeline_steps.py pipeline_store.py; do
  if [ ! -f "$f" ]; then
    echo "ABORT: $f tidak ditemukan." >&2
    exit 1
  fi
done

mkdir -p pipeline
TMP="$(mktemp -d)"
cleanup(){ rm -rf "$TMP"; }
trap cleanup EXIT

cp pipeline_routes.py  "$TMP/routes.py"
cp pipeline_helpers.py "$TMP/helpers.py"
cp pipeline_steps.py   "$TMP/steps.py"
cp pipeline_store.py   "$TMP/store.py"

cp "$TMP/routes.py"  pipeline/routes.py
cp "$TMP/helpers.py" pipeline/helpers.py
cp "$TMP/steps.py"   pipeline/steps.py
cp "$TMP/store.py"   pipeline/store.py

python - <<'PY'
from pathlib import Path
p = Path('pipeline/store.py')
s = p.read_text(encoding='utf-8')
old = '_BASE_DIR = os.path.dirname(os.path.abspath(__file__))'
new = '_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))'
if old not in s:
    raise SystemExit('ABORT: anchor _BASE_DIR standar tidak ditemukan di pipeline/store.py')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
print('PATCH OK: pipeline/store.py (_BASE_DIR naik 1 level)')
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

# Update imports in moved files to package-local modules. Root shims remain, but
# direct package imports are cleaner and avoid bouncing through compatibility shims.
python - <<'PY'
from pathlib import Path
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
    for old, new in mp.items():
        s = s.replace(old, new)
    p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: pipeline/* memakai package-local imports')
PY

python -m py_compile \
  pipeline/__init__.py pipeline/routes.py pipeline/helpers.py pipeline/steps.py pipeline/store.py \
  pipeline_routes.py pipeline_helpers.py pipeline_steps.py pipeline_store.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add pipeline/__init__.py pipeline/routes.py pipeline/helpers.py pipeline/steps.py pipeline/store.py \
        pipeline_routes.py pipeline_helpers.py pipeline_steps.py pipeline_store.py

git commit -m "PR-5: pindahkan modul pipeline ke paket pipeline"

echo
printf '%s\n' "OK: commit PR-5 reorg pipeline dibuat LOKAL (belum di-push)."
printf '%s\n' "LANGKAH BERIKUT:"
printf '%s\n' "1) python -c \"import pipeline_routes, pipeline_helpers, pipeline_steps, pipeline_store; import pipeline.routes, pipeline.helpers, pipeline.steps, pipeline.store; print('PIPELINE REORG OK')\""
printf '%s\n' "2) python web_app.py"
printf '%s\n' "3) Cek /tools?action=state, /tools, Step 6/9/10 load-save bila sempat."
printf '%s\n' "4) Bila OK: git push"
printf '%s\n' "5) Bila gagal: git reset --hard HEAD~1"
