@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   CCRG Build Script
echo   Output: dist\ccrg\ccrg.exe + ml_lib
echo ========================================
echo.

:: ========== 1. Environment ==========
cd /d "%~dp0\..\..\.."
echo [1/5] Project dir: %CD%
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] .venv\Scripts\activate.bat not found
    echo Create venv: python -m venv .venv
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [WARN] pyinstaller not found, installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] pyinstaller install failed
        pause
        exit /b 1
    )
)

:: ========== 2. Clean old build ==========
echo [2/5] Cleaning old build artifacts...
if exist "build" (
    rd /s /q "build" 2>nul
    if exist "build" echo   [WARN] build dir locked, skipping...
)
if exist "dist\ccrg" (
    rd /s /q "dist\ccrg" 2>nul
    if exist "dist\ccrg" echo   [WARN] dist\ccrg dir locked, skipping...
)
if exist "dist\ccrg.exe" (
    del /f /q "dist\ccrg.exe" 2>nul
)
echo   Clean done
echo.

:: ========== 3. PyInstaller build ==========
echo [3/5] PyInstaller building (ccrg.spec)...
echo   This may take several minutes, please wait...
echo.

set CODEBUDDY_SESSION_ID=
set CLAUDE_SESSION_ID=

pyinstaller ccrg.spec --noconfirm 2>&1

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed! Check errors above.
    pause
    exit /b 1
)
echo   Build done
echo.

:: ========== 4. Copy ML libs ==========
echo [4/5] Copying ML libs to dist\ccrg\ml_lib\...
python build_copy_ml_lib.py 2>&1

if errorlevel 1 (
    echo.
    echo [WARN] ML lib copy failed! exe still works (keyword routing only).
    echo   For semantic routing, install torch/sentence_transformers in .venv.
    echo.
) else (
    echo   ML libs copied
)
echo.

:: ========== 5. Verify ==========
echo [5/5] Verifying build artifacts...
echo.

set "BUILD_OK=1"

if not exist "dist\ccrg\ccrg.exe" (
    echo   [MISSING] dist\ccrg\ccrg.exe
    set "BUILD_OK=0"
)
if not exist "dist\ccrg\.gateway.json" (
    echo   [MISSING] dist\ccrg\.gateway.json
    set "BUILD_OK=0"
)
if not exist "dist\ccrg\keywords.json" (
    echo   [MISSING] dist\ccrg\keywords.json
    set "BUILD_OK=0"
)
if not exist "dist\ccrg\_internal" (
    echo   [MISSING] dist\ccrg\_internal
    set "BUILD_OK=0"
)

if "%BUILD_OK%"=="0" (
    echo.
    echo [ERROR] Build artifacts incomplete!
    pause
    exit /b 1
)

set "EXE_SIZE=0"
for %%A in ("dist\ccrg\ccrg.exe") do set "EXE_SIZE=%%~zA"
set /a "EXE_MB=%EXE_SIZE% / 1048576"

echo   [OK] dist\ccrg\ccrg.exe  (%EXE_MB% MB)
echo   [OK] dist\ccrg\.gateway.json
echo   [OK] dist\ccrg\keywords.json
echo   [OK] dist\ccrg\_internal\

if exist "dist\ccrg\ml_lib" (
    echo   [OK] dist\ccrg\ml_lib\  (semantic routing available)
) else (
    echo   [--] dist\ccrg\ml_lib\  (keyword routing only)
)

echo.
echo ========================================
echo   Build complete!
echo.
echo   Run:
echo     cd dist\ccrg
echo     ccrg.exe
echo.
echo   Or double-click: dist\ccrg\ccrg.exe
echo   Port: 3428  |  Console: http://127.0.0.1:3428/stats
echo ========================================
echo.

pause
endlocal