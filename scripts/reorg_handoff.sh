#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu." >&2
  exit 1
fi

PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
if [ -z "$PY" ]; then
  echo "ABORT: Python tidak ditemukan." >&2
  exit 1
fi

echo "Python runner: $PY"

for f in handoff_routes.py handoff_routing_db.py handoff_routing_patch.py; do
  if [ ! -f "$f" ]; then echo "ABORT: $f tidak ditemukan." >&2; exit 1; fi
done

mkdir -p handoff
cat > handoff/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket handoff (perutean layanan) internal Camerad Studio.

Berisi route kelola tabel perutean, storage handoff_routing, dan patch yang
menyisipkan panduan perutean (mandiri/agent/KPP) ke RAG saat menjawab.
Root module lama tetap tersedia sebagai shim kompatibilitas.
"""
PY

cp handoff_routes.py handoff/routes.py
cp handoff_routing_db.py handoff/routing_db.py
cp handoff_routing_patch.py handoff/routing_patch.py

$PY - <<'PY'
from pathlib import Path
repls = {
    Path('handoff/routes.py'): {
        'import handoff_routing_db as hrdb': 'from handoff import routing_db as hrdb',
    },
    Path('handoff/routing_patch.py'): {
        'import handoff_routing_db as _hrdb': 'from handoff import routing_db as _hrdb',
    },
}
for p, mp in repls.items():
    s = p.read_text(encoding='utf-8')
    for old, new in mp.items():
        s = s.replace(old, new)
    p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: handoff/* memakai package-local imports')
PY

cat > handoff_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routes.py.
import sys as _sys
import handoff.routes as _mod
_sys.modules[__name__] = _mod
PY
cat > handoff_routing_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routing_db.py.
import sys as _sys
import handoff.routing_db as _mod
_sys.modules[__name__] = _mod
PY
cat > handoff_routing_patch.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-7). Asli dipindah ke handoff/routing_patch.py.
import sys as _sys
import handoff.routing_patch as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  handoff/__init__.py handoff/routes.py handoff/routing_db.py handoff/routing_patch.py \
  handoff_routes.py handoff_routing_db.py handoff_routing_patch.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add handoff handoff_routes.py handoff_routing_db.py handoff_routing_patch.py

git commit -m "PR-7: pindahkan modul handoff ke paket handoff"

echo
echo "OK: commit PR-7 handoff dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import handoff.routes, handoff.routing_db, handoff.routing_patch; import handoff_routes, handoff_routing_db, handoff_routing_patch; print('HANDOFF OK')\""
echo "Boot: python web_app.py -> kalau hijau: git push"
