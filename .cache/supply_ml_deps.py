"""补全 dist_nu/ccrg/ml_lib 缺失依赖（按 Requires-Dist 闭包，从源 site-packages 复制）。
只读操作：不修改源环境；缺失项复制到 ml_lib。
"""
import os
import re
import shutil
import sys
import importlib.metadata as imd
from packaging.requirements import Requirement

MARKER_ENV = {
    "extra": "",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "sys_platform": "win32",
    "os_name": "nt",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
}

ML = r"D:\Users\viaco\PycharmProjects\minimax-ccr\dist_nu\ccrg\ml_lib"
SP = r"C:\Users\viaco\.conda\envs\ccrg312\Lib\site-packages"

ROOTS = ["torch", "transformers", "sentence_transformers", "tokenizers",
         "huggingface_hub", "safetensors"]

NEVER = {"pip", "setuptools", "wheel", "pyinstaller", "pytest", "build",
         "pywin32", "pywin32-ctypes", "mypy", "ruff", "pooch"}


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def deps_of(pkg: str):
    try:
        d = imd.distribution(pkg)
    except Exception:
        return []
    reqs = []
    for r in (d.requires or []):
        try:
            req = Requirement(r)
        except Exception:
            continue
        if req.marker is not None:
            try:
                if not req.marker.evaluate(MARKER_ENV):
                    continue
            except Exception:
                continue
        reqs.append(norm(req.name))
    return reqs


def build_index(root: str):
    """site-packages 条目 -> {规范名: [条目路径]}"""
    idx = {}
    try:
        entries = os.listdir(root)
    except FileNotFoundError:
        return idx
    for e in entries:
        full = os.path.join(root, e)
        if e.endswith(".dist-info"):
            name = e[: -len(".dist-info")].split("-")[0]
            idx.setdefault(norm(name), []).append(full)
        elif e.endswith(".egg-info"):
            name = e[: -len(".egg-info")].split("-")[0]
            idx.setdefault(norm(name), []).append(full)
        elif e.endswith(".libs"):
            name = e[: -len(".libs")]
            idx.setdefault(norm(name), []).append(full)
        else:
            # 普通目录或单文件（.py/.pyd/.so）
            idx.setdefault(norm(e), []).append(full)
    return idx


SP_IDX = build_index(SP)
ML_IDX = build_index(ML)


def in_ml(pkg: str) -> bool:
    return norm(pkg) in ML_IDX


def copy_pkg(pkg: str):
    n = norm(pkg)
    targets = SP_IDX.get(n, [])
    copied = 0
    for src in targets:
        base = os.path.basename(src)
        dst = os.path.join(ML, base)
        if os.path.isdir(src):
            if base.endswith(".dist-info"):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copytree(src, dst, dirs_exist_ok=True,
                                ignore=lambda d, names: [x for x in names if x == "__pycache__"])
        else:
            shutil.copy2(src, dst)
        copied += 1
    return copied


def main():
    queue = list(ROOTS)
    seen = set()
    missing = []
    while queue:
        p = queue.pop(0)
        if p in seen:
            continue
        seen.add(p)
        if not in_ml(p) and p not in NEVER:
            missing.append(p)
        for d in deps_of(p):
            if d not in seen:
                queue.append(d)

    if not missing:
        print("all declared deps present in ml_lib")
        return

    print(f"missing {len(missing)} deps:")
    for m in sorted(missing):
        print("  -", m)

    print("\ncopying...")
    for m in sorted(missing):
        n = copy_pkg(m)
        print(f"  {m}: copied {n} item(s)")
    print("done")


if __name__ == "__main__":
    main()
