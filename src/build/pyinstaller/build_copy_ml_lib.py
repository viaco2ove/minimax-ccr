"""
构建后步骤：把重型 ML 栈(torch / sentence_transformers / transformers / ...)作为真实包
选择性拷进 <DIST>/ccrg/ml_lib/，作为 exe 的外部引用库。

分两步：
1. 白名单复制：按 ML_PACKAGES 把 ML 核心包及其直接依赖拷进 ml_lib/；
2. Requires-Dist 闭包补齐：按依赖闭包扫描 roots 的全部无条件运行时依赖，
   把 ml_lib 缺失的纯 Python 依赖（如 narwhals）补进来（对齐 supply_ml_deps.py）。

只拷贝 ML 相关包及其传递依赖，不拷贝已冻结进 exe 的包（uvicorn/fastapi/httpx/click 等）。
这样 ml_lib 在 sys.path[0] 时，非 ML 包仍从 _internal/ 正常解析。

运行时 runtime_ml_lib_hook.py 会把 <DIST>/ccrg/ml_lib/ 注入 sys.path，
于是 `import torch` / `from sentence_transformers import ...` 在 frozen 环境也能正常解析。

ml_lib 缺失时 exe 自动降级 keyword 路由，不崩溃。

配置统一读自同目录 pyinstaller.ini（pyinstaller_cfg），site-packages 来源不硬编码。
"""
import os
import shutil
import sys

import importlib.metadata as md
from packaging.requirements import Requirement

from pyinstaller_cfg import cfg

VENV_SP = cfg.SITE_PACKAGES
DST = cfg.ML_LIB

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
    # narwhals (新版 scikit-learn 的运行时依赖；部分环境 requires 未声明，
    # 闭包可能扫不到，故加入白名单显式复制)
    "narwhals",
}

# 这些永远不会拷贝（构建工具或已冻结进 exe）
NEVER_COPY = {
    "PyInstaller", "pyinstaller", "pip", "setuptools", "wheel",
    "build", "pywin32", "pywin32-ctypes",
    # 已冻结进 exe 的包，不需要在 ml_lib 中
    "fastapi", "uvicorn", "httpx", "anyio", "starlette",
    "click", "h11", "sniffio",
}

# 闭包补齐阶段的跳过集：ML 核心大包已由白名单复制、Web 栈已冻结进 exe，
# 都不需要再经闭包补齐（其余小依赖如 narwhals 会由闭包自动补上）。
SKIP = NEVER_COPY | {
    "torch", "functorch", "torchgen",
    "sentence-transformers", "transformers", "tokenizers",
    "huggingface-hub", "safetensors",
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


# ---------- Requires-Dist 闭包补齐（对齐 supply_ml_deps.py） ----------

def _closure(roots):
    """收集 roots 的无条件运行时依赖闭包（不含 SKIP 自身）。

    种子与 SKIP 中的包仍会展开其 requires（它们的依赖可能是需要复制的包），
    但 SKIP 包自身不进入返回集合。
    """
    seen = set()
    visited = set()
    stack = list(roots)
    skip = {s.lower() for s in SKIP}
    while stack:
        name = stack.pop()
        norm = name.replace("_", "-").lower()
        if norm in visited:
            continue
        visited.add(norm)
        try:
            dist = md.distribution(norm)
        except md.PackageNotFoundError:
            continue
        for req_str in dist.requires or []:
            try:
                req = Requirement(req_str)
            except Exception:
                continue
            # 只取无条件（或 extra 为空）的运行时依赖
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                dep = req.name.replace("_", "-").lower()
                stack.append(req.name)
                if dep not in skip:
                    seen.add(dep)
    return seen


def _dist_dir(name):
    """返回 site-packages 中与包名匹配的目录/文件。"""
    norm = name.replace("_", "-").lower()
    for entry in os.listdir(VENV_SP):
        base = entry.split("-")[0].replace("_", "-").lower()
        if base == norm:
            return entry
        if entry.lower().endswith(".dist-info"):
            pkg = entry[:-len(".dist-info")].split("-")[0].replace("_", "-").lower()
            if pkg == norm:
                return entry
    return None


def _copy_top_level(entry):
    """按 dist-info/top_level.txt 补顶层模块文件。

    单文件模块（如 typing_extensions.py）只有 dist-info 没有同名目录，
    按发行名复制目录时极易漏掉 .py 本体；这里按 top_level 显式补齐。
    部分发行版（如 typing_extensions 4.16）不带 top_level.txt，此时
    以发行名推断单文件导入名兜底。
    """
    di_dir = os.path.join(VENV_SP, entry)
    tl = os.path.join(di_dir, "top_level.txt")
    if os.path.isfile(tl):
        with open(tl, encoding="utf-8") as f:
            tops = [ln.strip() for ln in f if ln.strip()]
    else:
        base = entry[:-len(".dist-info")] if entry.endswith(".dist-info") else entry
        tops = [base.split("-")[0].replace("-", "_")]
    for top in tops:
        for cand in (top + ".py", top):
            src = os.path.join(VENV_SP, cand)
            if not (os.path.isfile(src) or os.path.isdir(src)):
                continue
            dst = os.path.join(DST, cand)
            if os.path.exists(dst):
                break
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=lambda d, n: [x for x in n if x == "__pycache__"])
            else:
                shutil.copy2(src, dst)
            print(f"  + top-level {cand}")
            break


def supply_missing_deps():
    """按 Requires-Dist 闭包补齐 ml_lib 缺失依赖（如 narwhals）。"""
    os.makedirs(DST, exist_ok=True)
    roots = ["torch", "transformers", "sentence_transformers"]
    deps = sorted(_closure(roots))
    print(f"[build_copy_ml_lib] dependency closure: {len(deps)} packages")

    copied = 0
    for dep in deps:
        entry = _dist_dir(dep)
        if not entry:
            continue
        src = os.path.join(VENV_SP, entry)
        dst = os.path.join(DST, entry)
        if not os.path.exists(dst):
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=lambda d, n: [x for x in n if x == "__pycache__"])
            else:
                shutil.copy2(src, dst)
            copied += 1
            print(f"  + {entry}")
        # 单文件模块等顶层文件补齐（即使 dist-info 已存在也执行）
        _copy_top_level(entry)
    print(f"[build_copy_ml_lib] closure: copied {copied} missing package(s)")


def main():
    if not os.path.isdir(VENV_SP):
        print(f"[build_copy_ml_lib] site-packages 未找到: {VENV_SP}")
        sys.exit(1)

    # 第一步：白名单复制 ML 核心包
    to_copy = _find_package_dirs(VENV_SP)
    if not to_copy:
        print("[build_copy_ml_lib] 警告：未找到任何 ML 包")
    else:
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
        print(f"[build_copy_ml_lib] whitelist done. {len(to_copy)} packages, total {total_bytes/1e9:.2f} GB")

    # 第二步：闭包补齐缺失依赖
    supply_missing_deps()

    print(f"[build_copy_ml_lib] 完成。输出: {DST}")


if __name__ == "__main__":
    main()
