#!/usr/bin/env bash
# Entrypoint container: backend FastAPI (internal) + frontend FastAPI (0.0.0.0:8080).
set -u
cd /app

python llm_fix_final_combined.py &
PY_PID=$!

echo "Menunggu backend siap..."
for i in $(seq 1 90); do
  if curl -s "http://127.0.0.1:${PIPELINE_PORT:-8000}/health" >/dev/null 2>&1; then break; fi
  sleep 1
done

export WEB_HOST=0.0.0.0
export WEB_PORT=8080
python web_app.py

kill $PY_PID 2>/dev/null
