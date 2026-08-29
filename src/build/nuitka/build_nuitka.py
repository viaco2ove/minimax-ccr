#!/usr/bin/env python3
"""CCRG Nuitka 打包主脚本（配置化版）。

- 配置统一读自同目录 nuitka.ini（路径 / 环境 / 端口不硬编码）
- 编译完成后自动执行后处理 post_build_fix.py（补 stdlib、python3.dll、缺失依赖、固定端口）

用法：
    <打包环境python> src/build/nuitka/build_nuitka.py
    例如：C:/Users/viaco/.conda/envs/ccrg312/python.exe src/build/nuitka/build_nuitka.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from nuitka_cfg import cfg

DLL_LIST = [
    "ffi.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "sqlite3.dll", "libbz2.dll", "liblzma.dll", "libexpat.dll",
]


def ensure_nuitka():
    """打包环境缺 nuitka 时自动安装。"""
    py = cfg.require_env()
    out = subprocess.run([py, "-m", "pip", "show", "nuitka"], capture_output=True)
    if out.returncode != 0:
        print("[INFO] Installing nuitka...")
        subprocess.run([py, "-m", "pip", "install", "nuitka"], check=True)


def clean_old_dist():
    print("[2/7] Cleaning old dist...")
    for p in (cfg.DIST, cfg.BUILD_TEMP):
        if Path(p).exists():
            shutil.rmtree(p, ignore_errors=True)


def compile_nuitka():
    """调用 Nuitka 编译入口脚本（run_ccrg.py）。"""
    py = cfg.require_env()
    print("[3/7] Nuitka compiling (5-15 min)...")
    env = os.environ.copy()
    env["PYTHONPATH"] = cfg.SRC + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        py, "-m", "nuitka",
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
        # 允许被 nofollow 的模块在运行时从外部 sys.path（ml_lib/）加载，而非抛 actively excluded
        "--no-deployment-flag=excluded-module-usage",
        f"--output-dir={cfg.BUILD_TEMP}",
        cfg.ENTRY,
    ]
    res = subprocess.run(cmd, env=env, cwd=cfg.ROOT)
    if res.returncode != 0:
        raise RuntimeError("Nuitka build failed")


def rename_output():
    print("[4/7] Renaming output...")
    src = Path(cfg.BUILD_TEMP) / "run_ccrg.dist"
    if src.exists():
        if Path(cfg.DIST).exists():
            shutil.rmtree(cfg.DIST, ignore_errors=True)
        shutil.move(str(src), cfg.DIST)
    shutil.rmtree(cfg.BUILD_TEMP, ignore_errors=True)


def copy_configs():
    print("[5/7] Copying configs...")
    os.makedirs(os.path.join(cfg.DIST, "logs"), exist_ok=True)
    shutil.copy(cfg.GATEWAY, os.path.join(cfg.DIST, ".gateway.json"))
    shutil.copy(cfg.KEYWORDS, os.path.join(cfg.DIST, "keywords.json"))


def copy_runtime_dlls():
    print("[5b] Copying runtime DLLs...")
    candidates = []
    if cfg.CONDA_ENVS:
        candidates.append(os.path.join(cfg.CONDA_ENVS, "Library", "bin"))
    if cfg.CONDA_PATH:
        candidates.append(os.path.join(cfg.CONDA_PATH, "Library", "bin"))
    for dll in DLL_LIST:
        for lib in candidates:
            src = os.path.join(lib, dll)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(cfg.DIST, dll))
                break


def copy_ml_libs():
    print("[6/7] Copying ML libs to ml_lib/...")
    script = os.path.join(cfg.BUILD_DIR, "build_copy_ml_nu_lib.py")
    res = subprocess.run([cfg.require_env(), script], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[WARN] ML lib copy failed: {res.stderr}")
    else:
        print(res.stdout.strip())


def verify():
    print("[7/7] Verifying...")
    missing = [f for f in ("run_ccrg.exe", ".gateway.json", "keywords.json")
               if not os.path.isfile(os.path.join(cfg.DIST, f))]
    if missing:
        print(f"[ERROR] Missing in {cfg.DIST}: {missing}")
        sys.exit(1)
    exe_size = os.path.getsize(os.path.join(cfg.DIST, "run_ccrg.exe"))
    print(f"  [OK] {os.path.join(cfg.DIST, 'run_ccrg.exe')}  ({exe_size // 1024 // 1024} MB)")
    print("  [OK] .gateway.json / keywords.json / logs/")
    print("  [OK] ml_lib/" if os.path.isdir(cfg.ML_LIB) else "  [--] ml_lib/ (keyword only)")
    print("  Build complete!")


def main():
    print(f"ROOT    = {cfg.ROOT}")
    print(f"Python  = {cfg.require_env()}")
    print(f"Dist    = {cfg.DIST}")

    ensure_nuitka()
    clean_old_dist()
    compile_nuitka()
    rename_output()
    copy_configs()
    copy_runtime_dlls()
    copy_ml_libs()
    verify()

    # 自动执行后处理：补 stdlib 副本 / python3.dll / 缺失依赖 / 固定端口
    fix = os.path.join(cfg.BUILD_DIR, "post_build_fix.py")
    print("\n== Running post-build fix ==")
    subprocess.run([cfg.require_env(), fix], check=False)
    print("\nAll done. Run:  cd dist_nu/ccrg && run_ccrg.exe")


if __name__ == "__main__":
    main()
