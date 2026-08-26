"""CCRG 打包后的入口脚本"""
import sys, os, traceback, faulthandler

# 启用 faulthandler：C 级崩溃时自动 dump Python 堆栈到文件
_FAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faulthandler.log")
faulthandler.enable(file=open(_FAULT_LOG, "w", encoding="utf-8"))

# Nuitka multiprocessing 冻结检测：sys.frozen 必须设置
sys.frozen = True

from ccrg.main import run

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        if sys.stdin.isatty():
            input("\nPress Enter to exit...")
        sys.exit(1)
