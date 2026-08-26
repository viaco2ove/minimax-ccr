@echo off
cd /d "%~dp0dist_nu\ccrg"
echo Starting run_ccrg.exe...
run_ccrg.exe
echo Exit code: %ERRORLEVEL%
pause
