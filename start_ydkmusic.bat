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

echo Opening the website. Keep the server window open while using it.
start "ydkmusic server" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000"

echo ydkmusic is running at http://127.0.0.1:8000
endlocal
