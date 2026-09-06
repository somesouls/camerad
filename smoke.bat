@echo off
REM smoke.bat - jalankan harness uji-asap Camerad (kompilasi + smoke offline-safe).
REM Keluar dengan kode dari smoke_test.py (0 = lulus, selain itu gagal).
python smoke_test.py
exit /b %errorlevel%
