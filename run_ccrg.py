"""CCRG 打包后的入口脚本"""
from ccrg.main import run
import traceback, sys

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)
