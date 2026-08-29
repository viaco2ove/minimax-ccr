"""
构建后步骤：把重型 ML 栈(torch / sentence_transformers / transformers / ...)作为真实包
选择性拷进 dist/ccrg/ml_lib/，作为 exe 的外部引用库。

只拷贝 ML 相关包及其传递依赖，不拷贝已冻结进 exe 的包（uvicorn/fastapi/httpx/click 等）。
这样 ml_lib 在 sys.path[0] 时，非 ML 包仍从 _internal/ 正常解析。

运行时 runtime_ml_lib_hook.py 会把 dist/ccrg/ml_lib/ 注入 sys.path，
于是 `import torch` / `from sentence_transformers import ...` 在 frozen 环境也能正常解析。

ml_lib 缺失时 exe 自动降级 keyword 路由，不崩溃。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_SP = os.path.join(ROOT, "../../.venv", "Lib", "site-packages")
DST = os.path.join(ROOT, "dist", "ccrg", "ml_lib")

# ML 核心包 + 其传递依赖（import 名）
ML_PACKAGES = {
    # torch 家族
    "torch", "functorch", "torchgen",
    # sentence_transformers 及其依赖
    "sentence_transformers",
    # transformers 家族
    "transformers", "tokenizers",
    # huggingface 相关
    "huggingface_hub",
    # safetensors
    "safetensors",
    # numpy (torch 依赖)
    "numpy", "numpy.libs",
    # torch 的硬性依赖（import torch 时会用到）
    "sympy", "networkx", "mpmath",
    # 其他 ML 传递依赖
    "filelock", "fsspec", "Jinja2", "markupsafe",
    "packaging", "pyyaml", "_yaml", "yaml",
    "regex", "requests", "urllib3", "certifi", "charset_normalizer", "idna",
    "tqdm", "typing_extensions",
    "scipy", "scikit-learn", "sklearn",  # sentence_transformers 必需依赖（包目录名是 sklearn）
    "joblib", "threadpoolctl",
    "Pillow",  # 图像处理
}

# 这些永远不会拷贝（构建工具或已冻结进 exe）
NEVER_COPY = {
    "PyInstaller", "pyinstaller", "pip", "setuptools", "wheel",
    "build", "pywin32", "pywin32-ctypes",
    # 已冻结进 exe 的包，不需要在 ml_lib 中
    "fastapi", "uvicorn", "httpx", "anyio", "starlette",
    "click", "h11", "sniffio",
}


def _norm(name: str) -> str:
    """包名规范化：忽略大小写，连字符/下划线等价（PEP 503 风格），
    否则 'scikit-learn' 匹配不上磁盘上的 'scikit_learn-1.8.0.dist-info'"""
    return name.lower().replace("-", "_")


def _find_package_dirs(venv_sp: str) -> set:
    """找到 ML_PACKAGES 中每个包对应的实际目录/文件名（可能带版本后缀，也可能是单个 .py 文件）"""
    result = set()
    ml_norm = {_norm(p) for p in ML_PACKAGES}
    for entry in os.listdir(venv_sp):
        entry_lower = entry.lower()
        # 跳过安装包残件（.whl/.egg），避免把无用的归档文件拷进 ml_lib
        if entry_lower.endswith((".whl", ".egg")):
            continue
        # 单文件模块（如 typing_extensions.py / threadpoolctl.py）：
        # sentence_transformers 的必需依赖，漏拷会导致 frozen 环境 import 失败
        if entry_lower.endswith(".py"):
            if _norm(entry_lower[:-3]) in ml_norm:
                result.add(entry)
            continue
        # 直接匹配包名
        base_name = entry_lower.split("-")[0]  # e.g. "torch-2.12.0" -> "torch"
        if _norm(base_name) in ml_norm:
            result.add(entry)
        # 也匹配 _dist-info 目录
        if entry_lower.endswith(".dist-info"):
            pkg_name = entry_lower.replace(".dist-info", "").split("-")[0]
            if _norm(pkg_name) in ml_norm:
                result.add(entry)
        # 匹配 .libs 目录
        if entry_lower.endswith(".libs"):
            pkg_name = entry_lower.replace(".libs", "")
            if _norm(pkg_name) in ml_norm:
                result.add(entry)
    return result


def main():
    if not os.path.isdir(VENV_SP):
        print(f"[build_copy_ml_lib] site-packages 未找到: {VENV_SP}")
        sys.exit(1)

    # 找到要拷贝的目录
    to_copy = _find_package_dirs(VENV_SP)

    if not to_copy:
        print("[build_copy_ml_lib] 警告：未找到任何 ML 包")
        return

    # 清理旧 ml_lib 内容（只删 ml_lib 目录本身，不影响同级的 _internal/ 和 ccrg.exe）
    if os.path.exists(DST):
        try:
            shutil.rmtree(DST)
        except (OSError, FileNotFoundError):
            pass
    os.makedirs(DST, exist_ok=True)

    total_bytes = 0
    for pkg_dir in sorted(to_copy):
        src = os.path.join(VENV_SP, pkg_dir)
        dst = os.path.join(DST, pkg_dir)
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=False, copy_function=shutil.copy2,
                          ignore=lambda d, n: [x for x in n if x == "__pycache__"])
        else:
            shutil.copy2(src, dst)
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(dst) for f in fs)
        total_bytes += size
        print(f"  {pkg_dir}  ({size/1e6:.1f} MB)")

    print(f"\n[build_copy_ml_lib] 完成。共 {len(to_copy)} 个包，总计 {total_bytes/1e9:.2f} GB")
    print(f"[build_copy_ml_lib] 输出: {DST}")


if __name__ == "__main__":
    main()
