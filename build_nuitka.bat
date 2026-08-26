@echo off
setlocal

echo ========================================
echo ## winget install Microsoft.VisualStudio.2022.BuildTools
echo ## or conda python=3.12
echo conda create -n ccrg312 python=3.12 -y
echo conda activate ccrg312
echo pip install fastapi uvicorn httpx ...
echo need:conda activate ccrg312
echo   CCRG Nuitka Build Script
echo   Output: dist_nu\ccrg\run_ccrg.exe + ml_lib
echo ========================================
echo.

:: ========== 1. Environment ==========
cd /d "%~dp0"
echo [1/7] Project dir: %CD%
echo.

:: Activate conda env ccrg312 (Python 3.12, Nuitka-safe)
:: Python 3.13 + MSVC 14.2 on this box causes Nuitka segfault, so we use 3.12.
where conda >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conda not found in PATH
    echo Install Miniconda/Anaconda first.
    pause
    exit /b 1
)

call conda activate ccrg312
if errorlevel 1 (
    echo [ERROR] conda env ccrg312 not found
    echo Create it: conda create -n ccrg312 python=3.12 -y
    pause
    exit /b 1
)

pip show nuitka >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing nuitka in ccrg312...
    pip install nuitka
    if errorlevel 1 (
        echo [ERROR] nuitka install failed
        pause
        exit /b 1
    )
)

:: ========== 2. Clean old dist ==========
echo [2/7] Cleaning old dist...
if exist "dist_nu\ccrg" (
    rd /s /q "dist_nu\ccrg" 2>nul
    if exist "dist_nu\ccrg" echo   [WARN] dist_nu\ccrg locked, skipping...
)
echo   Clean done
echo.

:: ========== 3. Nuitka compile ==========
echo [3/7] Nuitka compiling...
echo   This may take 5-15 minutes, please wait...
echo.

python -m nuitka ^
  --standalone ^
  --windows-console-mode=attach ^
  --assume-yes-for-downloads ^
  --output-dir=dist_nu\ccrg_build_temp ^
  run_ccrg.py ^
  --follow-imports

if errorlevel 1 (
    echo.
    echo [ERROR] Nuitka build failed! Check errors above.
    pause
    exit /b 1
)
echo   Compile done
echo.

:: ========== 4. Rename output ==========
echo [4/8] Renaming output...
:: Nuitka outputs to dist_nu\ccrg_build_temp\run_ccrg.dist, rename to dist_nu\ccrg
if exist "dist_nu\ccrg" (
    rd /s /q "dist_nu\ccrg" 2>nul
    if exist "dist_nu\ccrg" echo   [WARN] dist_nu\ccrg locked
)
if exist "dist_nu\ccrg_build_temp\run_ccrg.dist" (
    move /y "dist_nu\ccrg_build_temp\run_ccrg.dist" "dist_nu\ccrg" >nul
    if exist "dist_nu\ccrg_build_temp\run_ccrg.dist" echo   [WARN] rename failed
)
rd /s /q "dist_nu\ccrg_build_temp" 2>nul
echo   Done
echo.

:: ========== 5. Create logs dir and copy configs ==========
echo [5/8] Creating logs dir and copying configs...
if not exist "dist_nu\ccrg\logs" mkdir "dist_nu\ccrg\logs"
copy /y ".gateway.json" "dist_nu\ccrg\.gateway.json" >nul
copy /y "keywords.json" "dist_nu\ccrg\keywords.json" >nul
echo   Done
echo.

:: ========== 6. Copy ML libs ==========
echo [6/8] Copying ML libs to dist_nu\ccrg\ml_lib\...
python build_copy_ml_nu_lib.py 2>&1

if errorlevel 1 (
    echo.
    echo [WARN] ML lib copy failed! exe still works (keyword routing only).
) else (
    echo   ML libs copied
)
echo.

:: ========== 7. Verify ==========
echo [7/8] Verifying build artifacts...
echo.

set "BUILD_OK=1"

if not exist "dist_nu\ccrg\run_ccrg.exe" (
    echo   [MISSING] dist_nu\ccrg\run_ccrg.exe
    set "BUILD_OK=0"
)
if not exist "dist_nu\ccrg\.gateway.json" (
    echo   [MISSING] dist_nu\ccrg\.gateway.json
    set "BUILD_OK=0"
)
if not exist "dist_nu\ccrg\keywords.json" (
    echo   [MISSING] dist_nu\ccrg\keywords.json
    set "BUILD_OK=0"
)

if "%BUILD_OK%"=="0" (
    echo.
    echo [ERROR] Build artifacts incomplete!
    pause
    exit /b 1
)

set "EXE_SIZE=0"
for %%A in ("dist_nu\ccrg\run_ccrg.exe") do set "EXE_SIZE=%%~zA"
set /a "EXE_MB=%EXE_SIZE% / 1048576"

echo   [OK] dist_nu\ccrg\run_ccrg.exe  (%EXE_MB% MB)
echo   [OK] dist_nu\ccrg\.gateway.json
echo   [OK] dist_nu\ccrg\keywords.json
echo   [OK] dist_nu\ccrg\logs\

if exist "dist_nu\ccrg\ml_lib" (
    echo   [OK] dist_nu\ccrg\ml_lib\  (semantic routing available)
) else (
    echo   [--] dist_nu\ccrg\ml_lib\  (keyword routing only)
)

echo.
echo ========================================
echo   Build complete!
echo.
echo   Run:
echo     cd dist_nu\ccrg
echo     run_ccrg.exe
echo.
echo   Port: 3428  |  Console: http://127.0.0.1:3428/stats
echo ========================================
echo.

pause
endlocal