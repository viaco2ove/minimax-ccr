@echo off
cd /d "%~dp0"

echo Starting mmx_provider (port def 3457) ...
start "mmx_provider" /min python mmx_provider.py
timeout /t 2 >/dev/null
