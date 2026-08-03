@echo off
REM ================================================================
REM  Jalankan Backend (FastAPI) + Frontend (FastAPI/web_app.py).
REM  Keduanya Python - tidak butuh PHP lagi.
REM  Buka dari PC lain: http://<IP-PC-INI>:8080/
REM ================================================================
cd /d "%~dp0"

if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat

REM Ambil beberapa nilai dari .env (kalau ada)
set "PIPELINE_API_KEY="
set "WEB_PORT="
if exist .env (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /I "%%A"=="PIPELINE_API_KEY" set "PIPELINE_API_KEY=%%B"
    if /I "%%A"=="WEB_PORT" set "WEB_PORT=%%B"
  )
)
if "%PIPELINE_API_KEY%"=="" set "PIPELINE_API_KEY=sam-n8n-secret"
if "%WEB_PORT%"=="" set "WEB_PORT=8080"
set "WEB_HOST=0.0.0.0"

echo Menjalankan backend Python (FastAPI)...
start "Backend Python (FastAPI)" cmd /k python llm_fix_final_combined.py

echo Menunggu backend siap (10 detik)...
timeout /t 10 /nobreak >nul

echo Menjalankan frontend (FastAPI/web UI) ...
start "Frontend (FastAPI)" cmd /k python web_app.py

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
