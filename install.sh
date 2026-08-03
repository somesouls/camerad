#!/usr/bin/env bash
# ================================================================
#  Install dependency Python (buat virtualenv .venv + install paket).
#  Semua komponen kini Python (tidak perlu PHP).
# ================================================================
set -e
cd "$(dirname "$0")"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip

# PyTorch CPU dulu (nanti bisa diganti versi CUDA setelah GPU ada)
pip install torch
pip install -r requirements.txt

echo
echo "Selesai. Langkah berikut:"
echo "  1) cp .env.example .env  &&  isi API key"
echo "  2) (opsional) taruh service-account.json di folder ini"
echo "  3) Jalankan ./start.sh  lalu buka http://<IP-PC-INI>:8080/"
