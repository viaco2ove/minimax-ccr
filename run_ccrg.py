"""CCRG 打包后的入口脚本"""
import sys, os
# 确保 src 目录在 import 路径中（兼容 PyInstaller / Nuitka / 直接运行）
_base = os.path.dirname(os.path.abspath(__file__))
for _src in (_base, os.path.join(_base, "ccrg")):
    if _src not in sys.path:
        sys.path.insert(0, _src)

from ccrg.main import run
import traceback

if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)
