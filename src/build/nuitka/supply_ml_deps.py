"""按 Requires-Dist 闭包扫描，把 ml_lib 缺失的运行时依赖从打包环境 site-packages
补进 dist_nu/ccrg/ml_lib/。配置统一读自同目录 nuitka.ini（nuitka_cfg）。

用途：Nuitka 打包后 ml_lib 是从外部复制的包集合，个别纯 Python 依赖（如 narwhals）
可能未进入复制清单，导致 exe 运行时 import 失败，这里做兜底补齐。
"""
import os
import shutil
import sys

import importlib.metadata as md
from packaging.requirements import Requirement

from nuitka_cfg import cfg

VENV_SP = cfg.SITE_PACKAGES
DST = cfg.ML_LIB

# 这些包即使在依赖闭包中出现，也不复制（构建/传输类，不应进入运行时 ml_lib）
# 注意：httpx/h11/sniffio/anyio 等 Web 基础库是 ml_lib 内 huggingface_hub 等包的
# 运行时依赖，必须保留在闭包中复制，不能列入 SKIP。
SKIP = {
    "torch", "sentence-transformers", "transformers", "tokenizers",
    "huggingface-hub", "safetensors",
    "pip", "setuptools", "wheel", "build", "pyinstaller",
    "fastapi", "uvicorn", "starlette",
    "click", "pywin32",
}


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


def main():
    if not os.path.isdir(VENV_SP):
        print(f"[supply_ml_deps] site-packages not found: {VENV_SP}")
        sys.exit(1)
    os.makedirs(DST, exist_ok=True)

    roots = ["torch", "transformers", "sentence_transformers"]
    deps = sorted(_closure(roots))
    print(f"[supply_ml_deps] dependency closure: {len(deps)} packages")

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
    print(f"[supply_ml_deps] copied {copied} missing package(s) to {DST}")


if __name__ == "__main__":
    main()
