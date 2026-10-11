@echo off
setlocal
cd /d "%~dp0"

echo Starting ydkmusic...

where py >nul 2>&1
if errorlevel 1 (
    echo Python is not installed yet.
    echo Please double-click setup_ydkmusic.bat once, then run this file again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    py -m venv .venv
    if errorlevel 1 (
        echo Could not create the Python environment. Make sure Python is installed.
        pause
        exit /b 1
    )
)

if not exist ".venv\.ydkmusic-dependencies-installed-v4" (
    echo Installing required packages for the first run...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Package installation failed.
        pause
        exit /b 1
    )
    type nul > ".venv\.ydkmusic-dependencies-installed-v4"
)

set "APP_PORT=8000"
set "HEALTH_FILE=%TEMP%\ydkmusic-health.json"
curl.exe --fail --silent http://127.0.0.1:8000/health > "%HEALTH_FILE%" 2>nul
findstr /C:"director_discovery_preview" "%HEALTH_FILE%" >nul 2>&1
if not errorlevel 1 goto :server_ready

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    set "APP_PORT=8001"
    echo Port 8000 is already occupied. Using port 8001 instead.
)

netstat -ano | findstr /R /C:":8001 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    set "APP_PORT=8002"
    echo Port 8001 is already occupied. Using port 8002 instead.
)

echo Opening the website. Keep the server window open while using it.
start "ydkmusic server" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT% --reload"
timeout /t 3 /nobreak >nul

:server_ready
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%APP_PORT%"

echo ydkmusic is running at http://127.0.0.1:%APP_PORT%
echo.
echo The ydkmusic server window will stay open while the app is running.
echo Close the window titled "ydkmusic server" or press Ctrl+C there to stop the server.
pause
endlocal
