@echo off
:: ============================================================
::  Sauce Dispenser - Windows Dev Launcher
::  Starts the FastAPI backend in a separate window,
::  waits for it to be ready, then opens the browser.
::
::  On Raspberry Pi use launch-backend.sh + Chromium kiosk.
:: ============================================================

setlocal EnableDelayedExpansion

:: -- Configuration -------------------------------------------
set PORT=8080
set URL=http://localhost:%PORT%/ui
:: ------------------------------------------------------------

:: Resolve the repo root (two levels up from ui\scripts\)
for %%I in ("%~dp0..\..") do set REPO_ROOT=%%~fI

echo.
echo  Sauce Dispenser - Dev Launcher
echo  =================================
echo  Repo : %REPO_ROOT%
echo  URL  : %URL%
echo.

:: -- Check Python is available -------------------------------
py --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python and add it to PATH.
    pause
    exit /b 1
)

:: -- Install dependencies if needed --------------------------
echo  Checking dependencies...
py -m pip install --quiet fastapi uvicorn pydantic
echo.

:: -- Start the FastAPI server in a new window ----------------
echo  Starting API server in a new window...
start "SauceBot API" cmd /k "cd /d "%REPO_ROOT%" && py -m pi.main"

:: -- Wait for the API to become ready ------------------------
echo  Waiting for API to be ready...
set RETRIES=20
:wait_loop
if %RETRIES%==0 goto not_ready
timeout /t 1 /nobreak >nul
curl -sf http://localhost:%PORT%/api/health >nul 2>&1
if not errorlevel 1 goto ready
set /a RETRIES=%RETRIES%-1
goto wait_loop

:not_ready
echo  [WARN] API did not respond in time - opening browser anyway.
goto open_browser

:ready
echo  API is ready.

:: -- Open browser --------------------------------------------
:open_browser
echo  Opening %URL%...
echo.
start msedge "%URL%" 2>nul || start chrome "%URL%" 2>nul || start "" "%URL%"

echo  Done. Close the "SauceBot API" window to stop the server.
echo.
endlocal
