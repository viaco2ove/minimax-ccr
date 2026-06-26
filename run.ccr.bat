@echo off
setlocal enabledelayedexpansion  :: 启用延迟扩展，确保路径处理正常
cd /d "%~dp0"  :: 切换到脚本所在目录（保持）

echo ========================================
echo   Claude Code Router Gateway
echo ========================================
echo.

:: 核心修复：用 call 执行激活脚本，确保虚拟环境生效
:: 同时增加容错：检查虚拟环境是否存在
if not exist ".venv\Scripts\activate.bat" (
    echo 错误：未找到虚拟环境 .venv，请先创建！
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat  :: call 是关键！确保激活后上下文延续

:: 激活后验证 Python 环境（可选，便于排查）
echo 当前 Python 路径：
where python
echo.

echo Starting CCRG (port def 3428) ...
python -m src.ccrg.main

:: 可选：退出虚拟环境（非必需，脚本结束后自动退出）
:: deactivate

pause
endlocal