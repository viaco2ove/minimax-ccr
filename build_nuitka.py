#!/usr/bin/env python3
"""
CCRG Nuitka Build Script (Python version)
- Auto-detect / create conda env ccrg312 (Python 3.12)
- Compile run_ccrg.py with Nuitka
- Copy configs, ML libs, runtime DLLs to dist_nu/ccrg/
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent
DIST = PROJ / "dist_nu" / "ccrg"
BUILD_TEMP = PROJ / "dist_nu" / "ccrg_build_temp"

# Known conda locations
CONDA_CANDIDATES = [
    Path(r"D:\ProgramData\miniconda3"),
    Path(r"C:\ProgramData\miniconda3"),
    Path(os.environ.get("USERPROFILE", r"C:\Users\viaco")) / "miniconda3",
    Path(os.environ.get("USERPROFILE", r"C:\Users\viaco")) / "anaconda3",
]

DLL_LIST = [
    "ffi.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "sqlite3.dll", "libbz2.dll", "liblzma.dll", "libexpat.dll",
]


def find_conda_root() -> Path | None:
    """Locate conda installation root."""
    # Try `conda info --base` first
    for exe in ["conda.exe", "conda.bat", "conda"]:
        try:
            out = subprocess.run(
                [exe, "info", "--base"], capture_output=True, text=True, timeout=10
            )
            if out.returncode == 0:
                base = out.stdout.strip().splitlines()[-1]
                p = Path(base)
                if p.exists():
                    return p
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    # Fallback to known locations
    for c in CONDA_CANDIDATES:
        if (c / "Scripts" / "conda.exe").exists():
            return c
    return None


def find_python_exe(conda_root: Path) -> Path:
    """Locate or create ccrg312 python.exe."""
    user_level = Path(os.environ.get("USERPROFILE", r"C:\Users\viaco")) / ".conda" / "envs" / "ccrg312" / "python.exe"
    standard = conda_root / "envs" / "ccrg312" / "python.exe"

    if user_level.exists():
        return user_level
    if standard.exists():
        return standard

    print(f"[1/7] Creating conda env ccrg312 (Python 3.12)...")
    conda_exe = conda_root / "Scripts" / "conda.exe"
    subprocess.run([str(conda_exe), "create", "-n", "ccrg312", "python=3.12", "-y"], check=True)

    # Re-check after creation
    if user_level.exists():
        return user_level
    if standard.exists():
        return standard
    raise RuntimeError("ccrg312 python.exe not found after creation")


def ensure_nuitka(python_exe: Path):
    """Install nuitka if missing."""
    out = subprocess.run(
        [str(python_exe), "-m", "pip", "show", "nuitka"], capture_output=True
    )
    if out.returncode != 0:
        print("[INFO] Installing nuitka...")
        subprocess.run([str(python_exe), "-m", "pip", "install", "nuitka"], check=True)


def clean_old_dist():
    print("[2/7] Cleaning old dist...")
    if DIST.exists():
        shutil.rmtree(DIST, ignore_errors=True)
    if BUILD_TEMP.exists():
        shutil.rmtree(BUILD_TEMP, ignore_errors=True)

# --windows-console-mode=attach ^（附加模式）命令行无日志
# --windows-console-mode=force ^ （强制模式）命令行有无日志
def compile_nuitka(python_exe: Path):
    print("[3/7] Nuitka compiling (5-15 min)...")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJ / "src") + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        str(python_exe), "-m", "nuitka",
        "--standalone",
        "--windows-console-mode=force",
        "--assume-yes-for-downloads",
        "--disable-plugin=anti-bloat",
        "--disable-plugin=multiprocessing",
        "--nofollow-import-to=torch",
        "--nofollow-import-to=torchvision",
        "--nofollow-import-to=sentence_transformers",
        "--nofollow-import-to=transformers",
        "--nofollow-import-to=huggingface_hub",
        "--nofollow-import-to=tokenizers",
        "--nofollow-import-to=safetensors",
        f"--output-dir={BUILD_TEMP}",
        "run_ccrg.py",
    ]
    res = subprocess.run(cmd, env=env, cwd=str(PROJ))
    if res.returncode != 0:
        raise RuntimeError("Nuitka build failed")


def rename_output():
    print("[4/7] Renaming output...")
    src = BUILD_TEMP / "run_ccrg.dist"
    if src.exists():
        if DIST.exists():
            shutil.rmtree(DIST, ignore_errors=True)
        shutil.move(str(src), str(DIST))
    shutil.rmtree(BUILD_TEMP, ignore_errors=True)


def copy_configs():
    print("[5/7] Copying configs...")
    (DIST / "logs").mkdir(exist_ok=True)
    shutil.copy(PROJ / ".gateway.json", DIST / ".gateway.json")
    shutil.copy(PROJ / "keywords.json", DIST / "keywords.json")


def copy_runtime_dlls():
    print("[5b] Copying runtime DLLs...")
    user_lib = Path(os.environ.get("USERPROFILE", r"C:\Users\viaco")) / ".conda" / "envs" / "ccrg312" / "Library" / "bin"
    conda_root = None
    for c in CONDA_CANDIDATES:
        if (c / "Scripts" / "conda.exe").exists():
            conda_root = c
            break
    candidates = [user_lib]
    if conda_root:
        candidates.append(conda_root / "Library" / "bin")

    for dll in DLL_LIST:
        for lib in candidates:
            src = lib / dll
            if src.exists():
                shutil.copy(src, DIST / dll)
                break


def copy_ml_libs(python_exe: Path):
    print("[6/7] Copying ML libs to ml_lib/...")
    res = subprocess.run(
        [str(python_exe), str(PROJ / "build_copy_ml_nu_lib.py")],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"[WARN] ML lib copy failed: {res.stderr}")
    else:
        print(res.stdout.strip())


def verify():
    print("[7/7] Verifying...")
    missing = []
    for f in ["run_ccrg.exe", ".gateway.json", "keywords.json"]:
        if not (DIST / f).exists():
            missing.append(f)
    if missing:
        print(f"[ERROR] Missing: {missing}")
        sys.exit(1)

    exe_size = (DIST / "run_ccrg.exe").stat().st_size
    print(f"  [OK] dist_nu/ccrg/run_ccrg.exe  ({exe_size // 1024 // 1024} MB)")
    print(f"  [OK] dist_nu/ccrg/.gateway.json")
    print(f"  [OK] dist_nu/ccrg/keywords.json")
    print(f"  [OK] dist_nu/ccrg/logs/")
    print(f"  [OK] dist_nu/ccrg/ml_lib/" if (DIST / "ml_lib").exists() else "  [--] dist_nu/ccrg/ml_lib/ (keyword only)")

    print("\n========================================")
    print("  Build complete!")
    print("  Run:  cd dist_nu\\ccrg && run_ccrg.exe")
    print("========================================")


def main():
    os.chdir(PROJ)
    print(f"[1/7] Project dir: {PROJ}")

    conda_root = find_conda_root()
    if not conda_root:
        print("[ERROR] conda not found")
        sys.exit(1)
    print(f"  Conda: {conda_root}")

    python_exe = find_python_exe(conda_root)
    print(f"  Python: {python_exe}")

    # Get version
    ver = subprocess.run([str(python_exe), "--version"], capture_output=True, text=True)
    print(f"  Python version: {ver.stdout.strip()}")

    ensure_nuitka(python_exe)
    clean_old_dist()
    compile_nuitka(python_exe)
    rename_output()
    copy_configs()
    copy_runtime_dlls()
    copy_ml_libs(python_exe)
    verify()


if __name__ == "__main__":
    main()