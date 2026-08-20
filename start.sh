#!/usr/bin/env bash
# ================================================================
#  Jalankan camerad dalam SATU proses: UI + RAG + Avaya AWE (web_app.py, 8080).
#  Selaras dengan start.bat.
#
#  Backend berat lama (llm_fix_final_combined.py, port 8000) kini OPSIONAL
#  dan hanya dibutuhkan untuk sebagian pekerjaan Step Dialogflow tertentu.
#  Nyalakan dengan:  ./start.sh --with-backend   (atau set WITH_BACKEND=1)
#
#  Buka dari PC lain: http://<IP-PC-INI>:8080/
# ================================================================
set -u
cd "$(dirname "$0")"

# Aktifkan venv bila ada
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# Muat .env (agar PIPELINE_API_KEY dll tersedia utk skrip ini)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
export PIPELINE_API_KEY="${PIPELINE_API_KEY:-sam-n8n-secret}"
WEB_PORT="${WEB_PORT:-8080}"
export WEB_HOST="${WEB_HOST:-0.0.0.0}"
export WEB_PORT

# Backend lama (opsional). Aktifkan via flag --with-backend atau WITH_BACKEND=1
WITH_BACKEND="${WITH_BACKEND:-0}"
if [ "${1:-}" = "--with-backend" ]; then WITH_BACKEND=1; fi

PY_PID=""
if [ "$WITH_BACKEND" = "1" ]; then
  echo "Menjalankan backend lama (llm_fix_final_combined.py, port ${PIPELINE_PORT:-8000}) ..."
  python llm_fix_final_combined.py &
  PY_PID=$!
  trap 'echo; echo "Menghentikan..."; [ -n "$PY_PID" ] && kill $PY_PID 2>/dev/null; exit 0' INT TERM
  echo "Menunggu backend siap..."
  for i in $(seq 1 90); do
    if curl -s "http://127.0.0.1:${PIPELINE_PORT:-8000}/health" >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "================================================================"
echo " camerad: UI + RAG + Avaya AWE (web_app.py) — satu proses"
echo " BUKA DI BROWSER PC INI : http://localhost:${WEB_PORT}/"
echo " (JANGAN buka http://0.0.0.0:${WEB_PORT} - itu bukan URL valid)"
echo " Dari PC lain di LAN    : http://${IP:-<IP-PC-INI>}:${WEB_PORT}/"
echo " Izinkan port ${WEB_PORT} di firewall."
[ "$WITH_BACKEND" = "1" ] && echo " Backend lama aktif di port ${PIPELINE_PORT:-8000}."
echo "================================================================"

python web_app.py

[ -n "$PY_PID" ] && kill $PY_PID 2>/dev/null
