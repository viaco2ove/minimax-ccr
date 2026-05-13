@echo off
cd /d "%~dp0"

echo Starting mmx_provider (port def 3457) ...
start "mmx_provider" /min /D "%~dp0" python mmx_provider.py
timeout /t 2 >nul 2>&1
