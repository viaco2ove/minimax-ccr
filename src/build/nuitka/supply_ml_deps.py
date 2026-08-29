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
SKIP = {
    "torch", "sentence-transformers", "transformers", "tokenizers",
    "huggingface-hub", "safetensors",
    "pip", "setuptools", "wheel", "build", "pyinstaller",
    "fastapi", "uvicorn", "httpx", "anyio", "starlette",
    "click", "h11", "sniffio", "pywin32",
}


def _closure(roots):
    seen = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        norm = name.replace("_", "-").lower()
        if norm in seen or norm in {s.lower() for s in SKIP}:
            continue
        seen.add(norm)
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
                stack.append(req.name)
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
        if os.path.exists(dst):
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=lambda d, n: [x for x in n if x == "__pycache__"])
        else:
            shutil.copy2(src, dst)
        copied += 1
        print(f"  + {entry}")
    print(f"[supply_ml_deps] copied {copied} missing package(s) to {DST}")


if __name__ == "__main__":
    main()
