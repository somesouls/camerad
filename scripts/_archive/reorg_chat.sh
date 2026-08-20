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

for f in chat_frontend_routes.py agent_chat_routes.py; do
  if [ ! -f "$f" ]; then echo "ABORT: $f tidak ditemukan." >&2; exit 1; fi
done

mkdir -p chat
cat > chat/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""Paket chat Camerad Studio.

Berisi route kanal tanya-AI:
  - frontend_routes : widget Live Chat (/livechat) + jembatan Dialogflow ES
                      (Opsi B echo/poll) untuk chat.html.
  - agent_routes    : chat RAG \"Agent Kring Pajak\" (halaman utama '/') +
                      halaman konfigurasi mesin RAG (/rag-agent, /rag-chatbot).

Root module lama (chat_frontend_routes, agent_chat_routes) tetap tersedia
sebagai shim kompatibilitas.
"""
PY

cp chat_frontend_routes.py chat/frontend_routes.py
cp agent_chat_routes.py chat/agent_routes.py

$PY - <<'PY'
from pathlib import Path
p = Path('chat/frontend_routes.py')
s = p.read_text(encoding='utf-8')
pairs = [
    ('    import df_webhook_db as dfdb', '    from df_webhook import db as dfdb'),
    ('    import df_webhook_routes as dfw', '    from df_webhook import routes as dfw'),
]
for old, new in pairs:
    assert old in s, f'ABORT: baris import tak ditemukan: {old!r}'
    s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('IMPORT PATCH OK: chat/frontend_routes.py memakai from df_webhook import db/routes (app_core/llm_client tetap)')
PY

cat > chat_frontend_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-10). Asli dipindah ke chat/frontend_routes.py.
import sys as _sys
import chat.frontend_routes as _mod
_sys.modules[__name__] = _mod
PY
cat > agent_chat_routes.py <<'PY'
# -*- coding: utf-8 -*-
# Shim kompatibilitas mundur (PR-10). Asli dipindah ke chat/agent_routes.py.
import sys as _sys
import chat.agent_routes as _mod
_sys.modules[__name__] = _mod
PY

$PY -m py_compile \
  chat/__init__.py chat/frontend_routes.py chat/agent_routes.py \
  chat_frontend_routes.py agent_chat_routes.py \
  web_app.py app_core.py

echo "GATE OK: py_compile lulus."

git add chat chat_frontend_routes.py agent_chat_routes.py

git commit -m "PR-10: pindahkan chat_frontend_routes & agent_chat_routes ke paket chat"

echo
echo "OK: commit PR-10 chat dibuat LOKAL (belum di-push)."
echo "Smoke: python -c \"import chat.frontend_routes, chat.agent_routes; import chat_frontend_routes, agent_chat_routes; print('CHAT OK')\""
echo "Boot: python web_app.py -> cek / (rag-agent) + /livechat + /rag-chatbot 200 OK -> git push"
