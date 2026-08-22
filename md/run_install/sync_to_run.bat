@echo off
:: 同步 minimax-ccr 源码到 minimax-ccr-run
:: 双击此脚本即可完成同步

set SRC=D:\Users\viaco\PycharmProjects\minimax-ccr\src
set DST=D:\Users\viaco\PycharmProjects\minimax-ccr-run\src

echo 正在同步源码到 run 环境...
xcopy /E /Y /I "%SRC%\ccrg" "%DST%\ccrg"
echo 同步完成！
pause
