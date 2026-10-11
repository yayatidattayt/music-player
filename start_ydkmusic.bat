@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo Starting ydkmusic...

if not exist ".venv\Scripts\python.exe" (
    echo The private Python environment is missing.
    echo Run setup_ydkmusic.bat first, then run this file again.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo Project packages are missing or incomplete.
    echo Run setup_ydkmusic.bat first, then run this file again.
    pause
    exit /b 1
)

where powershell >nul 2>&1
if errorlevel 1 (
    echo Windows PowerShell was not found. This Windows installation cannot start the browser automatically.
    pause
    exit /b 1
)

set "APP_PORT=8000"
set "HEALTH_FILE=%TEMP%\ydkmusic-health.json"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 | Select-Object -ExpandProperty Content | Set-Content -Encoding UTF8 '%HEALTH_FILE%'; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto :server_ready

set "APP_PORT="
for /L %%P in (8000,1,8010) do (
    netstat -ano | findstr /R /C:":%%P .*LISTENING" >nul 2>&1
    if errorlevel 1 if not defined APP_PORT set "APP_PORT=%%P"
)
if not defined APP_PORT (
    echo No free port was found between 8000 and 8010.
    echo Close another local development server and try again.
    pause
    exit /b 1
)
if not "!APP_PORT!"=="8000" echo Port 8000 is occupied. Using port !APP_PORT! instead.

echo Opening the website. Keep the server window open while using it.
start "ydkmusic server" /D "%CD%" cmd /k ""%CD%\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port !APP_PORT!"

set /a WAIT_COUNT=0
:wait_for_server
timeout /t 1 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:!APP_PORT!/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto :server_ready
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 20 (
    echo.
    echo The server did not start. Read the open ^"ydkmusic server^" window for the error.
    pause
    exit /b 1
)
goto :wait_for_server

:server_ready
start "" "http://127.0.0.1:!APP_PORT!/"

echo ydkmusic is running at http://127.0.0.1:!APP_PORT!
echo.
echo The ydkmusic server window will stay open while the app is running.
echo Close the window titled "ydkmusic server" or press Ctrl+C there to stop the server.
pause
endlocal
