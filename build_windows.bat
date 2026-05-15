@echo off
REM ===============================================================
REM  LG Load Optimizer — Windows local build script (PyInstaller)
REM
REM  Requirements:
REM    - Windows 10/11
REM    - Python 3.10 or 3.11 installed (https://python.org/downloads/)
REM    - ~2 GB free disk space (build scratch)
REM
REM  Usage:
REM    Double-click this file, or run from PowerShell/CMD.
REM    On first run it creates a build venv and installs deps (~5-10 min).
REM    Output: dist\LG_Load_Optimizer.exe (~250-400 MB)
REM ===============================================================

setlocal

echo [1/4] Creating build venv...
python -m venv .venv-build
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://python.org/downloads/ first.
    pause
    exit /b 1
)

echo [2/4] Activating venv + upgrading pip...
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip --quiet

echo [3/4] Installing dependencies (this may take a few minutes)...
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

echo [4/4] Building single-file exe with PyInstaller...
pyinstaller app.spec --clean --noconfirm

echo.
echo ============================================================
echo  Build complete.
echo  Exe location: dist\LG_Load_Optimizer.exe
echo ============================================================
echo.
echo  To run: double-click dist\LG_Load_Optimizer.exe
echo  First launch may take 10-30 seconds (cold start).
echo  Windows SmartScreen may warn "Unrecognized app" —
echo  click "More info" then "Run anyway" (it's your local binary).
echo.
pause
