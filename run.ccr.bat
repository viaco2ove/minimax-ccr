@echo off
cd /d "%~dp0"

echo ========================================
echo   Claude Code Router Gateway
echo ========================================
echo.

echo Starting CCRG (port def 3428) ...
python -m src.ccrg.main

pause
