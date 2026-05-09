@echo off
title UT Portfolio Dashboard
echo.
echo  ============================================================
echo    UT Portfolio Dashboard  ^|  http://127.0.0.1:5175
echo  ============================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found in PATH.
    echo  Please install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Move to the project folder (same location as this .bat file)
cd /d "%~dp0"

echo  Server starting... opening browser in 3 seconds.
echo  Press CTRL+C at any time to stop the dashboard.
echo.

REM Open the browser after a short delay (gives server time to boot)
ping -n 4 127.0.0.1 >nul
start "" "http://127.0.0.1:5175"

REM Start the FastAPI server (blocking — stays open until CTRL+C)
python -m uvicorn src.api:app --host 127.0.0.1 --port 5175

echo.
echo  Dashboard stopped.
pause
