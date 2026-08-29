"""CCRG 打包后的入口脚本"""
import sys, os, traceback, faulthandler

# 启用 faulthandler：C 级崩溃时自动 dump Python 堆栈到文件
_FAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faulthandler.log")
faulthandler.enable(file=open(_FAULT_LOG, "w", encoding="utf-8"))

# 注入 ml_lib 到 sys.path（让 sentence_transformers / torch 从外部目录加载）
_BASE = os.path.dirname(os.path.abspath(__file__))
_ML_LIB = os.path.join(_BASE, "ml_lib")
if os.path.isdir(_ML_LIB) and _ML_LIB not in sys.path:
    sys.path.insert(0, _ML_LIB)
    print(f"[run_ccrg] ml_lib injected: {_ML_LIB}", flush=True)

# --- Nuitka 外部 ML 库兼容修复 ---
# 保留 nuitka_module_loader（Nuitka 编译的项目包 ccrg 依赖它加载），
# 追加磁盘 stdlib 兜底 Finder：ml_lib 内 torch/transformers 等外部库依赖的
# stdlib 包内子模块（urllib.error、ctypes 等）标准查找会失败，改从 dist 目录
# 的 .py 副本强制加载。
import importlib.util as _ilu

class _StdlibFallbackFinder:
    _root = _BASE

    def find_spec(self, fullname, path=None, target=None):
        if fullname in sys.modules:
            return None
        rel = fullname.replace(".", "\\")
        for cand in (
            os.path.join(self._root, rel + ".py"),
            os.path.join(self._root, rel, "__init__.py"),
        ):
            if os.path.isfile(cand):
                try:
                    return _ilu.spec_from_file_location(fullname, cand)
                except Exception:
                    return None
        return None


sys.meta_path.append(_StdlibFallbackFinder())
print("[run_ccrg] stdlib fallback finder appended", flush=True)

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
