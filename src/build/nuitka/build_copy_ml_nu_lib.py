"""Nuitka 构建后步骤：把重型 ML 栈(torch/sentence_transformers/transformers/...)作为真实包
拷进 dist_nu/ccrg/ml_lib/，作为 exe 的外部引用库。

与 build_copy_ml_lib.py 功能相同，但目标路径为 dist_nu/ccrg/ml_lib/。
配置统一读自同目录 nuitka.ini（nuitka_cfg）。
"""
import os
import shutil
import sys

from nuitka_cfg import cfg

VENV_SP = cfg.SITE_PACKAGES
DST = cfg.ML_LIB

ML_PACKAGES = {
    "torch", "functorch", "torchgen",
    "sentence_transformers",
    "transformers", "tokenizers",
    "huggingface_hub",
    "safetensors",
    "numpy", "numpy.libs",
    "filelock", "fsspec", "Jinja2", "markupsafe",
    "packaging", "pyyaml", "_yaml", "yaml",
    "regex", "requests", "urllib3", "certifi", "charset_normalizer", "idna",
    "tqdm", "typing_extensions",
    "scipy", "scikit-learn",
    "sklearn", "scikit_learn",
    "joblib", "threadpoolctl",
    "Pillow",
}

NEVER_COPY = {
    "PyInstaller", "pyinstaller", "pip", "setuptools", "wheel",
    "build", "pywin32", "pywin32-ctypes",
    "fastapi", "uvicorn", "httpx", "anyio", "starlette",
    "click", "h11", "sniffio",
}


def _find_package_dirs(venv_sp: str) -> set:
    result = set()
    ml_lower = {p.lower() for p in ML_PACKAGES}
    for entry in os.listdir(venv_sp):
        entry_lower = entry.lower()
        base_name = entry_lower.split("-")[0]
        if base_name in ml_lower:
            result.add(entry)
        if entry_lower.endswith(".dist-info"):
            pkg_name = entry_lower.replace(".dist-info", "").split("-")[0]
            if pkg_name in ml_lower:
                result.add(entry)
        if entry_lower.endswith(".libs"):
            pkg_name = entry_lower.replace(".libs", "")
            if pkg_name in ml_lower:
                result.add(entry)
    return result


def main():
    if not os.path.isdir(VENV_SP):
        print(f"[build_copy_ml_nu_lib] site-packages not found: {VENV_SP}")
        sys.exit(1)

    to_copy = _find_package_dirs(VENV_SP)
    if not to_copy:
        print("[build_copy_ml_nu_lib] WARNING: no ML packages found")
        return

    if os.path.exists(DST):
        shutil.rmtree(DST)
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
        print(f"  {pkg_dir}  ({size / 1e6:.1f} MB)")

    print(f"\n[build_copy_ml_nu_lib] done. {len(to_copy)} packages, total {total_bytes / 1e9:.2f} GB")
    print(f"[build_copy_ml_nu_lib] output: {DST}")


if __name__ == "__main__":
    main()
