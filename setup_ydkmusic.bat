@echo off
setlocal
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links" set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"

title Set up ydkmusic
echo.
echo ========================================
echo          ydkmusic first-time setup
echo ========================================
echo.

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py"
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

where node >nul 2>&1
if errorlevel 1 (
    echo Node.js was not found. Installing the current LTS runtime...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo Node.js is required for the TypeScript tooling. Install it from https://nodejs.org/ and run this file again.
        pause
        exit /b 1
    )
    winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo Node.js installation failed.
        pause
        exit /b 1
    )
)

where npm >nul 2>&1
if errorlevel 1 (
    echo npm was not found. Restart this setup window after installing Node.js.
    pause
    exit /b 1
)

where tsc >nul 2>&1
if errorlevel 1 (
    echo Installing the TypeScript compiler...
    call npm install --global typescript
    if errorlevel 1 (
        echo TypeScript installation failed.
        pause
        exit /b 1
    )
)
if not defined PYTHON_CMD (
    echo Python was not found. Installing Python 3.12 through Windows Package Manager...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Windows Package Manager is unavailable on this computer.
        echo Install Python 3.12 from https://www.python.org/downloads/windows/
        echo Make sure to tick ^"Add python.exe to PATH^", then run this file again.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo Python installation failed.
        pause
        exit /b 1
    )
    echo Python was installed. Close this window, open a new one, and run setup_ydkmusic.bat again.
    pause
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the private project environment...
    "%PYTHON_CMD%" -m venv .venv
    if errorlevel 1 (
        echo Could not create the project environment.
        pause
        exit /b 1
    )
)

echo Installing Python packages. This can take a few minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo Could not upgrade pip. Continuing with the bundled pip...
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo Package installation failed.
    pause
    exit /b 1
)
type nul > ".venv\.ydkmusic-dependencies-installed-v4"

where ffmpeg >nul 2>&1
if errorlevel 1 if not exist "ffmpeg.exe" (
    echo.
    echo FFmpeg was not found. Installing it for YouTube importing...
    where winget >nul 2>&1
    if not errorlevel 1 (
        echo Installing FFmpeg through Windows Package Manager...
        winget install --id Gyan.FFmpeg -e --source winget --accept-source-agreements --accept-package-agreements
    ) else (
        echo Install FFmpeg from https://www.gyan.dev/ffmpeg/builds/ and add it to PATH.
    )
)

echo.
echo Checking toolchain...
where tsc >nul 2>&1 && echo TypeScript: ready || echo TypeScript: restart this window after setup
where ffmpeg >nul 2>&1 && echo FFmpeg: ready || echo FFmpeg: restart this window after setup

echo.
echo Setup complete. Double-click start_ydkmusic.bat to open ydkmusic.
pause
endlocal
