@echo off
cd /d "%~dp0"

echo ========================================
echo   Claude Code Router Gateway
echo ========================================
echo.

echo [1/2] Starting mmx_provider (port def 3457) ...
start "mmx_provider" /min python mmx_provider.py
timeout /t 2 >nul

echo [2/2] Starting CCRG (port def 3428) ...
python -m src.ccrg.main

pause
