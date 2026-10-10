@echo off
setlocal
cd /d "%~dp0"

echo Starting ydkmusic...

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    py -m venv .venv
    if errorlevel 1 (
        echo Could not create the Python environment. Make sure Python is installed.
        pause
        exit /b 1
    )
)

echo Checking required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Package installation failed.
    pause
    exit /b 1
)

set "APP_PORT=8000"
curl.exe --fail --silent http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 goto :server_ready

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    set "APP_PORT=8001"
    echo Port 8000 is already occupied. Using port 8001 instead.
)

echo Opening the website. Keep the server window open while using it.
start "ydkmusic server" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %APP_PORT% --reload"
timeout /t 3 /nobreak >nul

:server_ready
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%APP_PORT%"

echo ydkmusic is running at http://127.0.0.1:%APP_PORT%
endlocal
