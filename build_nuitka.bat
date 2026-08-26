@echo off
setlocal

echo ========================================
echo   CCRG Nuitka Build Script
echo   Output: dist_nu\ccrg\run_ccrg.exe + ml_lib
echo ========================================
echo.

:: ========== 1. Environment ==========
for %%I in ("%~f0") do set "BAT_DIR=%%~dpI"
set "BAT_DIR=%BAT_DIR:~0,-1%"
cd /d "%BAT_DIR%"
echo [1/7] Project dir: %CD%
echo.

:: Locate conda base (check known locations, most common first)
set "CONDA_ROOT="
if exist "D:\ProgramData\miniconda3\Scripts\conda.exe" set "CONDA_ROOT=D:\ProgramData\miniconda3"
if not defined CONDA_ROOT if exist "C:\ProgramData\miniconda3\Scripts\conda.exe" set "CONDA_ROOT=C:\ProgramData\miniconda3"
if not defined CONDA_ROOT if exist "%USERPROFILE%\miniconda3\Scripts\conda.exe" set "CONDA_ROOT=%USERPROFILE%\miniconda3"
if not defined CONDA_ROOT if exist "%USERPROFILE%\anaconda3\Scripts\conda.exe" set "CONDA_ROOT=%USERPROFILE%\anaconda3"
if not defined CONDA_ROOT (
    echo [ERROR] conda not found
    pause
    exit /b 1
)
echo   Conda: %CONDA_ROOT%

:: Python 3.12 env (Python 3.13 + MSVC 14.2 = segfault)
:: Check user-level conda envs dir first (C:\Users\viaco\.conda\envs\)
:: then standard conda envs dir
set "PYTHON_EXE=%USERPROFILE%\.conda\envs\ccrg312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%CONDA_ROOT%\envs\ccrg312\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [INFO] Creating conda env ccrg312 (Python 3.12)...
    "%CONDA_ROOT%\Scripts\conda.exe" create -n ccrg312 python=3.12 -y
    if errorlevel 1 (
        echo [ERROR] conda env creation failed
        pause
        exit /b 1
    )
    :: Try user-level path again after creation
    set "PYTHON_EXE=%USERPROFILE%\.conda\envs\ccrg312\python.exe"
)

:: Verify Python version
for /f "delims=" %%V in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   Python: %%V

:: Install nuitka if missing
"%PYTHON_EXE%" -m pip show nuitka >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing nuitka...
    "%PYTHON_EXE%" -m pip install nuitka
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

set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
set "CONDA_ROOT=%CONDA_ROOT%"
@REM   --windows-console-mode=attach ^
@REM  --windows-console-mode=force ^
"%PYTHON_EXE%" -m nuitka ^
  --standalone ^
  --windows-console-mode=force ^
  --assume-yes-for-downloads ^
  --disable-plugin=anti-bloat ^
  --disable-plugin=multiprocessing ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=torchvision ^
  --nofollow-import-to=sentence_transformers ^
  --nofollow-import-to=transformers ^
  --nofollow-import-to=huggingface_hub ^
  --nofollow-import-to=tokenizers ^
  --nofollow-import-to=safetensors ^
  --output-dir=dist_nu\ccrg_build_temp ^
  run_ccrg.py

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

:: ========== 5b. Copy missing runtime DLLs ==========
echo [5b] Copying runtime DLLs from conda env...
for %%D in (ffi.dll libcrypto-3-x64.dll libssl-3-x64.dll msvcp140.dll msvcp140_1.dll msvcp140_2.dll msvcp140_atomic_wait.dll sqlite3.dll libbz2.dll liblzma.dll libexpat.dll) do (
    if exist "%USERPROFILE%\.conda\envs\ccrg312\Library\bin\%%D" (
        copy /y "%USERPROFILE%\.conda\envs\ccrg312\Library\bin\%%D" "dist_nu\ccrg\" >nul 2>nul
    ) else if exist "%CONDA_ROOT%\Library\bin\%%D" (
        copy /y "%CONDA_ROOT%\Library\bin\%%D" "dist_nu\ccrg\" >nul 2>nul
    )
)
echo   Done
echo.

:: ========== 6. Copy ML libs ==========
echo [6/8] Copying ML libs to dist_nu\ccrg\ml_lib\...
"%PYTHON_EXE%" build_copy_ml_nu_lib.py 2>&1

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