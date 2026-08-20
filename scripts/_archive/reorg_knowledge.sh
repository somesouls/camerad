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

for f in knowledge_ctx.py knowledge_routes.py knowledge_semantic.py glossary_db.py disambig_db.py intentmap_db.py pustaka_stats.py; do
  if [ ! -f "$f" ]; then echo "ABORT: $f tidak ditemukan." >&2; exit 1; fi
done

mkdir -p knowledge
cat > knowledge/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket knowledge/pustaka internal Camerad Studio.

Berisi konteks pengetahuan analis, semantic retrieval pustaka, route Tanya AI,
dan storage glosarium/disambiguasi/peta intent/statistik pemakaian.
Root module lama tetap tersedia sebagai shim kompatibilitas.
"""
PY

cp knowledge_ctx.py knowledge/ctx.py
cp knowledge_routes.py knowledge/routes.py
cp knowledge_semantic.py knowledge/semantic.py
cp glossary_db.py knowledge/glossary_db.py
cp disambig_db.py knowledge/disambig_db.py
cp intentmap_db.py knowledge/intentmap_db.py
cp pustaka_stats.py knowledge/stats.py

$PY - <<'PY'
from pathlib import Path
repls = {
    Path('knowledge/ctx.py'): {
        'import glossary_db as gdb': 'from knowledge import glossary_db as gdb',
        'import disambig_db as ddb': 'from knowledge import disambig_db as ddb',
        'import intentmap_db as imdb': 'from knowledge import intentmap_db as imdb',
        'import pustaka_stats as pstats': 'from knowledge import stats as pstats',
        'import knowledge_semantic as ksem': 'from knowledge import semantic as ksem',
    },
    Path('knowledge/routes.py'): {
        'import glossary_db as gdb': 'from knowledge import glossary_db as gdb',
        'import disambig_db as ddb': 'from knowledge import disambig_db as ddb',
        'import intentmap_db as imdb': 'from knowledge import intentmap_db as imdb',
        'import knowledge_ctx as kctx': 'from knowledge import ctx as kctx',
    },
    Path('knowledge/semantic.py'): {
        'import glossary_db as gdb': 'from knowledge import glossary_db as gdb',
        'import disambig_db as ddb': 'from knowledge import disambig_db as ddb',
        'import intentmap_db as imdb': 'from knowledge import intentmap_db as imdb',
    },
}
for p, mp in repls.items():
    s = p.read_text(encoding='utf-8')
    for old, new in mp.items():
        s = s.replace(old, new)
    p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: knowledge/* memakai package-local imports')
PY

cat > knowledge_ctx.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/ctx.py.
import sys as _sys
import knowledge.ctx as _mod
_sys.modules[__name__] = _mod
PY
cat > knowledge_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/routes.py.
import sys as _sys
import knowledge.routes as _mod
_sys.modules[__name__] = _mod
PY
cat > knowledge_semantic.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/semantic.py.
import sys as _sys
import knowledge.semantic as _mod
_sys.modules[__name__] = _mod
PY
cat > glossary_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/glossary_db.py.
import sys as _sys
import knowledge.glossary_db as _mod
_sys.modules[__name__] = _mod
PY
cat > disambig_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/disambig_db.py.
import sys as _sys
import knowledge.disambig_db as _mod
_sys.modules[__name__] = _mod
PY
cat > intentmap_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/intentmap_db.py.
import sys as _sys
import knowledge.intentmap_db as _mod
_sys.modules[__name__] = _mod
PY
cat > pustaka_stats.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-6). Asli dipindah ke knowledge/stats.py.
import sys as _sys
import knowledge.stats as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  knowledge/__init__.py knowledge/ctx.py knowledge/routes.py knowledge/semantic.py \
  knowledge/glossary_db.py knowledge/disambig_db.py knowledge/intentmap_db.py knowledge/stats.py \
  knowledge_ctx.py knowledge_routes.py knowledge_semantic.py glossary_db.py disambig_db.py intentmap_db.py pustaka_stats.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add knowledge knowledge_ctx.py knowledge_routes.py knowledge_semantic.py glossary_db.py disambig_db.py intentmap_db.py pustaka_stats.py

git commit -m "PR-6: pindahkan modul knowledge ke paket knowledge"

echo
echo "OK: commit PR-6 knowledge dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import knowledge.ctx, knowledge.routes, knowledge.semantic; import glossary_db, disambig_db, intentmap_db, pustaka_stats; print('KNOWLEDGE OK')\""
echo "Boot: python web_app.py -> kalau hijau: git push"
