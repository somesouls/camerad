@echo off
REM ================================================================
REM  Jalankan Camerad Studio dalam SATU proses Python:
REM    APLIKASI WEB + MESIN RAG + endpoint AWE Avaya (web_app.py, 8080)
REM
REM  Catatan:
REM  - Analisis AWE Avaya kini terpasang langsung di web_app.py melalui
REM    avaya_web_bootstrap.py, jadi tidak perlu membuka terminal backend 8000
REM    hanya untuk /api/avaya-*.
REM  - Endpoint lama di llm_fix_final_combined.py tetap ada untuk kompatibilitas
REM    pekerjaan Step Dialogflow tertentu, tetapi tidak dijalankan otomatis oleh
REM    start.bat agar operasi harian cukup satu terminal.
REM  - Buka dari PC lain: http://<IP-PC-INI>:8080/
REM ================================================================
cd /d "%~dp0"

if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

REM Ambil WEB_PORT dari .env (kalau ada)
set "WEB_PORT="
if exist .env (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="WEB_PORT" set "WEB_PORT=%%B"
  )
)
if "%WEB_PORT%"=="" set "WEB_PORT=8080"
set "WEB_HOST=0.0.0.0"

echo Menjalankan aplikasi web + mesin RAG + Avaya AWE (web_app.py, port %WEB_PORT%) ...
start "Camerad Studio (web + RAG + Avaya, port %WEB_PORT%) - buka localhost:%WEB_PORT%" cmd /k python web_app.py

echo.
echo ================================================================
echo  BUKA DI BROWSER PC INI : http://localhost:%WEB_PORT%/
echo  ( JANGAN buka http://0.0.0.0:%WEB_PORT% - itu bukan URL valid )
echo.
echo  Untuk akses dari PC lain di LAN, pakai IP PC ini:
for /f "tokens=2 delims=:" %%I in ('ipconfig ^| findstr /c:"IPv4"') do (
  set "IPADDR=%%I"
  call echo    http://%%IPADDR: =%%:%WEB_PORT%/
)
echo  Izinkan port %WEB_PORT% di Windows Firewall bila perlu.
echo ================================================================
pause
