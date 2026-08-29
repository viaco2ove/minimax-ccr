#!/usr/bin/env python3
"""CCRG PyInstaller 打包主脚本（配置化版）。

- 配置统一读自同目录 pyinstaller.ini（路径 / 环境 / 端口不硬编码）
- 打包前自动 taskkill 正在运行的 ccrg.exe，避免 _internal/DLL 被占用导致覆盖失败
- 编译产物为 <DIST>/ccrg/ccrg.exe + _internal + ml_lib
- 编译完成后自动执行后处理 post_build_fix.py（补 python3.dll、固定端口）
- 可选 --update：额外生成精简更新版到 cfg.DIST_UPDATE
              （不含 .gateway.json / keywords.json / logs / *.db）

用法：
    <打包环境python> src/build/pyinstaller/build_pyinstaller.py [--update]
    例如：D:/Users/viaco/PycharmProjects/minimax-ccr/.venv/Scripts/python.exe \
          src/build/pyinstaller/build_pyinstaller.py --update
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pyinstaller_cfg import cfg


def ensure_pyinstaller():
    """打包环境缺 pyinstaller 时自动安装。"""
    py = cfg.require_env()
    out = subprocess.run([py, "-m", "pip", "show", "pyinstaller"], capture_output=True)
    if out.returncode != 0:
        print("[INFO] Installing pyinstaller...")
        subprocess.run([py, "-m", "pip", "install", "pyinstaller"], check=True)


def stop_running_exe():
    """结束正在运行的 ccrg.exe，避免 _internal/DLL 被占用导致覆盖失败。"""
    if os.name == "nt":
        res = subprocess.run(["taskkill", "/f", "/im", "ccrg.exe"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            print("[INFO] Stopped running ccrg.exe (DLL lock released)")
        else:
            print("[INFO] No running ccrg.exe to stop")
    else:
        subprocess.run(["pkill", "-f", "ccrg"], capture_output=True)


def clean_old_dist():
    print("[2/7] Cleaning old dist...")
    for p in (cfg.DIST, cfg.BUILD_TEMP):
        if Path(p).exists():
            shutil.rmtree(p, ignore_errors=True)


def run_pyinstaller():
    """调用 PyInstaller 编译 ccrg.spec（onedir）。"""
    py = cfg.require_env()
    print("[3/7] PyInstaller compiling (5-15 min)...")
    spec = os.path.join(cfg.BUILD_DIR, "ccrg.spec")
    env = os.environ.copy()
    env["PYTHONPATH"] = cfg.SRC + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        py, "-m", "PyInstaller", "--noconfirm", "--clean",
        f"--distpath={cfg.DIST_BASE}", f"--workpath={cfg.BUILD_TEMP}",
        spec,
    ]
    res = subprocess.run(cmd, env=env, cwd=cfg.ROOT)
    if res.returncode != 0:
        raise RuntimeError("PyInstaller build failed")


def copy_configs():
    print("[5/7] Copying configs...")
    os.makedirs(os.path.join(cfg.DIST, "logs"), exist_ok=True)
    shutil.copy(cfg.GATEWAY, os.path.join(cfg.DIST, ".gateway.json"))
    shutil.copy(cfg.KEYWORDS, os.path.join(cfg.DIST, "keywords.json"))


def copy_ml_libs():
    print("[6/7] Copying ML libs to ml_lib/...")
    script = os.path.join(cfg.BUILD_DIR, "build_copy_ml_lib.py")
    res = subprocess.run([cfg.require_env(), script], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[WARN] ML lib copy failed: {res.stderr}")
    else:
        print(res.stdout.strip())


def verify():
    print("[7/7] Verifying...")
    missing = [f for f in ("ccrg.exe", ".gateway.json", "keywords.json")
               if not os.path.isfile(os.path.join(cfg.DIST, f))]
    if missing:
        print(f"[ERROR] Missing in {cfg.DIST}: {missing}")
        sys.exit(1)
    exe_size = os.path.getsize(os.path.join(cfg.DIST, "ccrg.exe"))
    print(f"  [OK] {os.path.join(cfg.DIST, 'ccrg.exe')}  ({exe_size // 1024 // 1024} MB)")
    print("  [OK] .gateway.json / keywords.json / logs/")
    print("  [OK] ml_lib/" if os.path.isdir(cfg.ML_LIB) else "  [--] ml_lib/ (keyword only)")
    print("  Build complete!")


# 精简更新版需剔除的运行时数据（配置/日志/数据库），其余程序本体全部保留
_RUNTIME_DATA_NAMES = {".gateway.json", "keywords.json", "logs", "faulthandler.log", "__pycache__"}


def _ignore_runtime_data(directory, names):
    """copytree 的 ignore 回调：过滤配置文件、日志与数据库。"""
    ignored = set()
    for n in names:
        if n in _RUNTIME_DATA_NAMES or n.endswith(".db") or n.endswith(".log"):
            ignored.add(n)
    return ignored


def make_update_copy():
    """从完整 dist 复制出精简更新版到 cfg.DIST_UPDATE。

    不含 .gateway.json / keywords.json / logs / *.db；
    适合作为增量更新包覆盖已有安装目录，保留原目录中的配置与数据。
    """
    dst = cfg.DIST_UPDATE
    print(f"\n== Generating update package: {dst} ==")
    if not os.path.isdir(cfg.DIST):
        raise RuntimeError(f"dist not found: {cfg.DIST}，请先完成打包")
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(cfg.DIST, dst, ignore=_ignore_runtime_data)
    print(f"  [OK] update package -> {dst}  (configs / logs / *.db excluded)")


def main():
    print(f"ROOT    = {cfg.ROOT}")
    print(f"Python  = {cfg.require_env()}")
    print(f"Dist    = {cfg.DIST}")
    print(f"Update  = {cfg.DIST_UPDATE}" if "--update" in sys.argv else "")

    ensure_pyinstaller()
    stop_running_exe()
    clean_old_dist()
    run_pyinstaller()
    copy_configs()
    copy_ml_libs()
    verify()

    # 自动执行后处理：补 python3.dll / 固定端口
    fix = os.path.join(cfg.BUILD_DIR, "post_build_fix.py")
    print("\n== Running post-build fix ==")
    subprocess.run([cfg.require_env(), fix], check=False)

    if "--update" in sys.argv:
        make_update_copy()

    print("\nAll done. Run:  cd dist_py/ccrg && ccrg.exe")


if __name__ == "__main__":
    main()
