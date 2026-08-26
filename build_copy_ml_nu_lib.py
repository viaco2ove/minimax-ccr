"""
Nuitka 构建后步骤：把重型 ML 栈(torch/sentence_transformers/transformers/...)作为真实包
拷进 dist_nu/ccrg/ml_lib/，作为 exe 的外部引用库。

与 build_copy_ml_lib.py 功能相同，但目标路径为 dist_nu/ccrg/ml_lib/。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
import os as _os
_USER = _os.environ.get("USERPROFILE", r"C:\Users\viaco")
# ccrg312 may be in user-level .conda/envs/ or standard conda envs/
_CANDIDATES = [
    _os.path.join(_USER, ".conda", "envs", "ccrg312"),
    _os.path.join(_os.environ.get("CONDA_ROOT", r"D:\ProgramData\miniconda3"), "envs", "ccrg312"),
]
VENV_SP = None
for _c in _CANDIDATES:
    _sp = _os.path.join(_c, "Lib", "site-packages")
    if _os.path.isdir(_sp):
        VENV_SP = _sp
        break
if VENV_SP is None:
    print(f"[build_copy_ml_nu_lib] site-packages not found in any candidate: {_CANDIDATES}")
    sys.exit(1)
DST = _os.path.join(ROOT, "dist_nu", "ccrg", "ml_lib")

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
    for entry in os.listdir(venv_sp):
        entry_lower = entry.lower()
        base_name = entry_lower.split("-")[0]
        if base_name in {p.lower() for p in ML_PACKAGES}:
            result.add(entry)
        if entry_lower.endswith(".dist-info"):
            pkg_name = entry_lower.replace(".dist-info", "").split("-")[0]
            if pkg_name in {p.lower() for p in ML_PACKAGES}:
                result.add(entry)
        if entry_lower.endswith(".libs"):
            pkg_name = entry_lower.replace(".libs", "")
            if pkg_name in {p.lower() for p in ML_PACKAGES}:
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