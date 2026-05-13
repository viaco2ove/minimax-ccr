@echo off
cd /d "%~dp0"

echo ========================================
echo   Claude Code Router Gateway
echo ========================================
echo.

call run.mmx_p.bat

echo Starting CCRG (port def 3428) ...
python -m src.ccrg.main

pause
