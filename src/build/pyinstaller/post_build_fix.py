"""重打包 ccrg.exe 后的必备后处理（PyInstaller 版）：
1. 补 python3.dll（tokenizers DLL 依赖）
2. 将 dist .gateway.json 端口固定为 pyinstaller.ini 中的 port

用法: 用 pyinstaller.ini 指定环境的 python.exe 执行本脚本
（一般由 build_pyinstaller.py 在打包完成后自动调用）
"""
import io
import json
import os
import shutil

from pyinstaller_cfg import cfg

DIST = cfg.DIST
PORT = cfg.PORT


def copy_py3_dll():
    print("[1/2] copying python3.dll ...")
    target = os.path.join(DIST, "python3.dll")
    if os.path.isfile(target):
        print("  python3.dll already present")
        return
    candidates = []
    if cfg.CONDA_ENVS:
        candidates.append(cfg.CONDA_ENVS)          # conda env 根
    if cfg.CONDA_PATH:
        candidates.append(cfg.CONDA_PATH)          # conda 安装根
    if cfg.PYTHON_EXE:
        candidates.append(os.path.dirname(cfg.PYTHON_EXE))  # venv/Scripts
    for c in candidates:
        src = os.path.join(c, "python3.dll")
        if os.path.isfile(src):
            shutil.copy2(src, target)
            print(f"  python3.dll copied from {src}")
            return
    print("  [WARN] python3.dll not found in candidates")


def set_port():
    print(f"[2/2] setting port to {PORT} ...")
    gw = os.path.join(DIST, ".gateway.json")
    obj = json.loads(io.open(gw, encoding="utf-8-sig").read())
    obj["server"]["port"] = PORT
    io.open(gw, "w", encoding="utf-8", newline="\n").write(
        json.dumps(obj, ensure_ascii=False, indent=2))
    print(f"  .gateway.json port -> {PORT}")


if __name__ == "__main__":
    if not os.path.isdir(DIST):
        raise SystemExit(f"[post_build_fix] dist not found: {DIST}，请先完成打包")
    copy_py3_dll()
    set_port()
    print("post-build fix done.")
