#!/usr/bin/env bash
# reorg_db.sh — PR-12: kelompokkan lapisan penyimpanan (SQLite) ke paket db/
# agent_log_db, analytics_db, qa_index_db, users_db -> db/*
# root dipertahankan sebagai shim (import <mod> & from <mod> import x tetap jalan).
# Nol rewrite: semua import di qa_index_db bersifat absolute top-level,
# tetap resolve via shim root (regref/pii_mask/text_norm/peraturan_*/sosmed_db/avaya_db/awe_botfilter_patch).
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ABORT: working tree tidak bersih. Commit/stash dulu."; exit 1
fi

PY=""
for c in python py python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if [ "$c" = "py" ]; then PY="py -3"; else PY="$c"; fi
    break
  fi
done
[ -n "$PY" ] || { echo "ABORT: python tidak ditemukan di PATH"; exit 1; }
echo "Python runner: $PY"

for f in agent_log_db.py analytics_db.py qa_index_db.py users_db.py; do
  [ -f "$f" ] || { echo "ABORT: berkas sumber $f tidak ada"; exit 1; }
done

mkdir -p db

cat > db/__init__.py <<'PY'
# -*- coding: utf-8 -*-
"""paket db: lapisan penyimpanan SQLite bersama.

Berisi: agent_log_db, analytics_db, qa_index_db, users_db.
Modul lama di root dipertahankan sebagai shim (kompatibilitas import).
"""
PY

cp agent_log_db.py db/agent_log_db.py
cp analytics_db.py db/analytics_db.py
cp qa_index_db.py  db/qa_index_db.py
cp users_db.py     db/users_db.py

for m in agent_log_db analytics_db qa_index_db users_db; do
cat > "$m.py" <<PY
import sys as _sys
import db.$m as _mod
_sys.modules[__name__] = _mod
PY
done

$PY -m py_compile \
  db/__init__.py db/agent_log_db.py db/analytics_db.py db/qa_index_db.py db/users_db.py \
  agent_log_db.py analytics_db.py qa_index_db.py users_db.py \
  web_app.py app_core.py
echo "GATE OK: py_compile lulus."

git add db/__init__.py db/agent_log_db.py db/analytics_db.py db/qa_index_db.py db/users_db.py \
        agent_log_db.py analytics_db.py qa_index_db.py users_db.py
git commit -m "PR-12: kelompokkan lapisan penyimpanan ke paket db (agent_log_db, analytics_db, qa_index_db, users_db)"

echo
echo "OK: commit PR-12 db dibuat LOKAL (belum di-push)."
echo 'Smoke: python -c "import db.agent_log_db, db.analytics_db, db.qa_index_db, db.users_db; import agent_log_db, analytics_db, qa_index_db, users_db; print(\"DB OK\")"'
echo "Boot: python web_app.py -> cek / (rag-agent) + /data + /users + /rag-chatbot 200 OK -> git push"
