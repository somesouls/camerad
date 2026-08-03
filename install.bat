@echo off
REM ================================================================
REM  Install dependency Python (virtualenv .venv + paket).
REM  Semua komponen kini Python (tidak perlu PHP).
REM ================================================================
cd /d "%~dp0"

python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

REM PyTorch CPU dulu (ganti versi CUDA setelah GPU ada)
pip install torch
pip install -r requirements.txt

echo.
echo Selesai. Langkah berikut:
echo   1) copy .env.example .env  lalu isi API key
echo   2) (opsional) taruh service-account.json di folder ini
echo   3) Jalankan start.bat lalu buka http://<IP-PC-INI>:8080/
pause
