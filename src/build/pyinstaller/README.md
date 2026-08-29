---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce997c05230104345033aec2d643180c_e6cf1acda3b911f193c6525400f8a581
    ReservedCode1: JWOokUX7pnCe4XZAL+kSwk7zrJ5l7RvvPh5Ho6qq1qf4dpbIfi86GzfxxtXyMYIrI75G1Vk90KC3v9UwEM7bMV5pjNII6mb32wES+z+O9KJYK2MgCSJmYwRnpIJP2fLgOMHK2B1XfOYztwn1GfJKqHZXqPMFuEM8H83XC6JvwJcGOuI8n1JGf2KMpl4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce997c05230104345033aec2d643180c_e6cf1acda3b911f193c6525400f8a581
    ReservedCode2: JWOokUX7pnCe4XZAL+kSwk7zrJ5l7RvvPh5Ho6qq1qf4dpbIfi86GzfxxtXyMYIrI75G1Vk90KC3v9UwEM7bMV5pjNII6mb32wES+z+O9KJYK2MgCSJmYwRnpIJP2fLgOMHK2B1XfOYztwn1GfJKqHZXqPMFuEM8H83XC6JvwJcGOuI8n1JGf2KMpl4=
---

# PyInstaller 打包方案（CCRG）

将重型 ML 栈（torch / sentence_transformers / transformers 等）排除在 exe 之外，作为真实包放在
`<DIST>/ccrg/ml_lib/`，运行时由 `runtime_ml_lib_hook.py` 注入 `sys.path` 加载。
`ml_lib` 缺失时 exe 自动降级 keyword 路由，不崩溃。

## 目录结构

| 文件 | 说明 |
| --- | --- |
| `pyinstaller.ini` | 本机配置（路径 / 环境 / 端口），换机器按 example 修改 |
| `pyinstaller_cfg.py` | ini 配置加载器，供各脚本 `import pyinstaller_cfg` 使用 |
| `ccrg.spec` | PyInstaller 打包配置（onedir，conda DLL / 路径均读自 ini） |
| `build_pyinstaller.py` | 打包主脚本：taskkill 旧进程 → 编译 → 拷配置 → 拷 ml_lib → 后处理 |
| `post_build_fix.py` | 后处理：补 python3.dll、固定 `.gateway.json` 端口 |
| `build_copy_ml_lib.py` | 白名单复制 ML 栈 + Requires-Dist 闭包补齐缺失依赖 |
| `runtime_ml_lib_hook.py` | 运行时把 `ml_lib/` 注入 `sys.path` |

## 用法

```bat
:: 完整打包
D:\...\.venv\Scripts\python.exe src\build\pyinstaller\build_pyinstaller.py

:: 打包 + 生成精简更新包（剔除 .gateway.json / keywords.json / logs / *.db）
D:\...\.venv\Scripts\python.exe src\build\pyinstaller\build_pyinstaller.py --update
```

产物：`<DIST>/ccrg/`（`ccrg.exe` + `_internal/` + `ml_lib/` + `.gateway.json` + `keywords.json` + `logs/`）

## 与 Nuitka 方案的关键差异

- 打包环境：`pyinstaller.ini` 中 `py_exe`（本项目用 `.venv`，Python 3.13 + PyInstaller）
- `ml_lib` 来源：`site_packages`（本项目用 `.venv/Lib/site-packages`）
- 缺失 stdlib 子模块：PyInstaller 用 `ccrg.spec` 的 `hiddenimports` 显式收集（Nuitka 用 .py 副本 + fallback finder）
- `numpy` 冻结进 exe（不在 excludes），与 Nuitka `--nofollow-import-to` 名单不含 numpy 对齐，
  保证 `semantic_local.py` 顶层 `import numpy` 在 ml_lib 缺失时也能启动
*（内容由AI生成，仅供参考）*
