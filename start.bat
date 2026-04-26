@echo off
REM ============================================================
REM Netflix Cookies Checker - Start script (Windows)
REM ============================================================
REM Khoi dong web tai http://127.0.0.1:5000 voi Waitress + cau hinh
REM toi uu cho local. Dong cua so terminal de tat server.
REM ============================================================

setlocal

cd /d "%~dp0"

REM ── Cau hinh (co the chinh) ─────────────────────────────────
if "%PORT%"=="" set PORT=5000
if "%BULK_WORKERS%"=="" set BULK_WORKERS=15
if "%WAITRESS_THREADS%"=="" set WAITRESS_THREADS=12

echo.
echo ============================================================
echo  Netflix Cookies Checker
echo  URL:             http://127.0.0.1:%PORT%
echo  BULK_WORKERS:    %BULK_WORKERS%   (so cookie xu ly song song)
echo  WAITRESS_THREADS:%WAITRESS_THREADS%
echo ============================================================
echo.
echo  Tip: tang BULK_WORKERS neu IP "nguoi", giam neu bi rate-limit.
echo  Dong cua so nay de tat server.
echo.

REM ── Mo browser sau 3 giay (khong block server) ──────────────
start "" /b cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:%PORT%"

REM ── Chay Waitress ────────────────────────────────────────────
python app.py

endlocal
