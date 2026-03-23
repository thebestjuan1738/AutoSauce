@echo off
:: ============================================================
::  Sauce Dispenser – Windows Test Launcher
::  Opens the UI in a local browser using Python's HTTP server.
::
::  On Raspberry Pi this role is filled by launch.sh
::  (same logic, Chromium kiosk mode instead of Edge/Chrome)
:: ============================================================

setlocal

:: ── Configuration ────────────────────────────────────────────
set PORT=8080
set URL=http://localhost:%PORT%
:: ─────────────────────────────────────────────────────────────

echo.
echo  Sauce Dispenser – Local Test Server
echo  =====================================
echo  Port : %PORT%
echo  URL  : %URL%
echo.

:: Check Python is available
py --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Please install Python and add it to PATH.
    echo  Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Move to the directory this batch file lives in
cd /d "%~dp0"

:: Open the browser a moment after the server starts.
:: Using 'start' so it runs without blocking the server.
:: Tries Microsoft Edge first (built-in on all modern Windows),
:: then falls back to Chrome, then the system default browser.
echo  Opening browser in 2 seconds...
start "" cmd /c "timeout /t 2 /nobreak >nul && (start msedge --kiosk %URL% --edge-kiosk-type=fullscreen 2>nul || start chrome --kiosk %URL% 2>nul || start %URL%)"

:: Start the Python HTTP server (blocks until Ctrl+C)
echo  Starting HTTP server (press Ctrl+C to stop)...
echo.
py -m http.server %PORT%

endlocal
