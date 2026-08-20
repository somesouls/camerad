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

for f in df_webhook_routes.py df_webhook_db.py; do
  if [ ! -f "$f" ]; then echo "ABORT: $f tidak ditemukan." >&2; exit 1; fi
done

mkdir -p df_webhook
cat > df_webhook/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket df_webhook (webhook Dialogflow ES chatbot Kring Pajak) Camerad Studio.

Berisi route fulfillment/echo-replay (Opsi B) + halaman & API konfigurasi, dan
storage konfigurasi webhook. Root module lama tetap tersedia sebagai shim
kompatibilitas.
"""
PY

cp df_webhook_routes.py df_webhook/routes.py
cp df_webhook_db.py df_webhook/db.py

$PY - <<'PY'
from pathlib import Path
p = Path('df_webhook/routes.py')
s = p.read_text(encoding='utf-8')
old = 'import df_webhook_db as dfdb'
new = 'from df_webhook import db as dfdb'
assert old in s, f'ABORT: baris import tak ditemukan: {old}'
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: df_webhook/routes.py memakai from df_webhook import db (rag_engine/agent_log_db/app_core tetap)')
PY

cat > df_webhook_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-9). Asli dipindah ke df_webhook/routes.py.
import sys as _sys
import df_webhook.routes as _mod
_sys.modules[__name__] = _mod
PY
cat > df_webhook_db.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-9). Asli dipindah ke df_webhook/db.py.
import sys as _sys
import df_webhook.db as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  df_webhook/__init__.py df_webhook/routes.py df_webhook/db.py \
  df_webhook_routes.py df_webhook_db.py \
  web_app.py app_core.py chat_frontend_routes.py

echo "GATE OK: py_compile lulus."

git add df_webhook df_webhook_routes.py df_webhook_db.py

git commit -m "PR-9: pindahkan modul df_webhook ke paket df_webhook"

echo
echo "OK: commit PR-9 df_webhook dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import df_webhook.routes, df_webhook.db; import df_webhook_routes, df_webhook_db; print('DFWEBHOOK OK')\""
echo "Boot: python web_app.py -> cek /df-webhook + /api/df/webhook/config 200 OK -> git push"
