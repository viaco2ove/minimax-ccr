"""重编 run_ccrg.exe 后的必备后处理：
1. 复制 conda 纯 stdlib .py 副本到 dist（兜底 Finder 依赖）
2. 补 python3.dll（tokenizers DLL 依赖）
3. 补 ml_lib 缺失依赖（调用 supply_ml_deps.py）
4. 将 dist .gateway.json 端口固定为 2048
用法: 用 ccrg312 环境的 python.exe 执行本脚本
"""
import os
import io
import json
import shutil
import subprocess
import sys

ROOT = r"D:\Users\viaco\PycharmProjects\minimax-ccr"
DIST = os.path.join(ROOT, "dist_nu", "ccrg")
ENV = r"C:\Users\viaco\.conda\envs\ccrg312"
PORT = 2048


def copy_stdlib():
    print("[1/4] copying stdlib .py to dist ...")
    src = os.path.join(ENV, "Lib")

    def ignore(d, names):
        return [n for n in names
                if n in ("__pycache__", "site-packages", "ml_lib") or n.endswith(".pyc")]

    shutil.copytree(src, DIST, dirs_exist_ok=True, ignore=ignore)
    print("  stdlib copied")


def copy_py3_dll():
    print("[2/4] copying python3.dll ...")
    dll = os.path.join(ENV, "python3.dll")
    if os.path.isfile(dll) and not os.path.isfile(os.path.join(DIST, "python3.dll")):
        shutil.copy2(dll, DIST)
        print("  python3.dll copied")
    else:
        print("  python3.dll already present / skipped")


def supply_deps():
    print("[3/4] supplying missing ml_lib deps ...")
    script = os.path.join(ROOT, ".cache", "supply_ml_deps.py")
    subprocess.run([sys.executable, script], check=False)


def set_port():
    print(f"[4/4] setting port to {PORT} ...")
    gw = os.path.join(DIST, ".gateway.json")
    obj = json.loads(io.open(gw, encoding="utf-8-sig").read())
    obj["server"]["port"] = PORT
    io.open(gw, "w", encoding="utf-8", newline="\n").write(
        json.dumps(obj, ensure_ascii=False, indent=2))
    print(f"  .gateway.json port -> {PORT}")


if __name__ == "__main__":
    copy_stdlib()
    copy_py3_dll()
    supply_deps()
    set_port()
    print("post-build fix done.")
