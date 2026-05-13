@echo off
cd /d "%~dp0"

echo Starting mmx_provider (port def 3457) ...
@REM start "mmx_provider" /min /D "%~dp0" python mmx_provider.py
@REM timeout /t 2 >nul 2>&1
python mmx_provider.py

