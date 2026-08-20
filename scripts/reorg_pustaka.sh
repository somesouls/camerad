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

if [ ! -f pustaka_routes.py ]; then echo "ABORT: pustaka_routes.py tidak ditemukan." >&2; exit 1; fi
if [ ! -d knowledge ]; then echo "ABORT: paket knowledge/ tidak ada (jalankan reorg knowledge dulu)." >&2; exit 1; fi

mkdir -p pustaka
cat > pustaka/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket pustaka (pustaka pengetahuan) Camerad Studio.

Berisi route kelola Glosarium, Disambiguasi, dan Peta/Katalog Intent.
Sumber data (glossary_db, disambig_db, intentmap_db, stats) berada di paket
knowledge/. Root module lama tetap tersedia sebagai shim kompatibilitas.
"""
PY

cp pustaka_routes.py pustaka/routes.py

$PY - <<'PY'
from pathlib import Path
p = Path('pustaka/routes.py')
s = p.read_text(encoding='utf-8')
repls = {
    'import glossary_db as gdb': 'from knowledge import glossary_db as gdb',
    'import disambig_db as ddb': 'from knowledge import disambig_db as ddb',
    'import intentmap_db as imdb': 'from knowledge import intentmap_db as imdb',
    'import pustaka_stats as pstats': 'from knowledge import stats as pstats',
}
for old, new in repls.items():
    assert old in s, f'ABORT: baris import tak ditemukan: {old}'
    s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: pustaka/routes.py memakai import knowledge.* (intent_describe & app_core tetap)')
PY

cat > pustaka_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-8). Asli dipindah ke pustaka/routes.py.
import sys as _sys
import pustaka.routes as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  pustaka/__init__.py pustaka/routes.py \
  pustaka_routes.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add pustaka pustaka_routes.py

git commit -m "PR-8: pindahkan pustaka_routes ke paket pustaka"

echo
echo "OK: commit PR-8 pustaka dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import pustaka.routes; import pustaka_routes; print('PUSTAKA OK')\""
echo "Boot: python web_app.py -> cek /glossary /disambig /intentmap 200 OK -> git push"
