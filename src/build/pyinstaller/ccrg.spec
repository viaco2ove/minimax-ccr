# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置 (onedir + 外部 ml_lib 引用库)
# 运行: src/build/pyinstaller/build_pyinstaller.py   （推荐，自动处理全部步骤）
#   或: <打包环境python> -m PyInstaller --noconfirm --clean ccrg.spec
#
# 设计：重型 ML 栈(torch / sentence_transformers / transformers / tokenizers /
# safetensors / huggingface_hub) 不冻结进 exe，而是作为真实包放在
# <DIST>/ccrg/ml_lib/，运行时由 runtime_ml_lib_hook.py 注入 sys.path 后正常 import。
# 这样：
#   1. exe 体积小、启动快，且避免 PyInstaller 搬运 torch 原生 dll 导致的 segfault；
#   2. ml_lib 缺失时 exe 仍正常运行(自动降级 keyword 路由，不崩溃)；
#   3. 放入 ml_lib 即启用语义路由，等于"装插件升级"。
# 注意：numpy 不在 excludes 中（冻结进 PYZ）——semantic_local.py 顶层 `import numpy`，
# ml_lib 缺失时也必须能启动；这与 Nuitka --nofollow-import-to 名单不含 numpy 对齐。
# 构建后需运行 build_copy_ml_lib.py 把 ML 栈拷进 ml_lib/（build_pyinstaller.py 会自动执行）。
#
# 路径 / 环境 / 端口统一读自同目录 pyinstaller.ini（pyinstaller_cfg），无硬编码。

import os
import sys

_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
if _SPEC_DIR not in sys.path:
    sys.path.insert(0, _SPEC_DIR)

from pyinstaller_cfg import cfg  # noqa: E402

# conda 环境中的系统 DLL，PyInstaller 无法自动解析（路径来自 pyinstaller.ini conda_path）
_CONDA_BIN = os.path.join(cfg.CONDA_PATH, "Library", "bin")
_CONDA_DLLS = [
    "sqlite3.dll", "ffi.dll", "liblzma.dll", "libbz2.dll",
    "libmpdec-4.dll", "libexpat.dll",
]
_binaries = [
    (os.path.join(_CONDA_BIN, dll), ".")
    for dll in _CONDA_DLLS
    if os.path.isfile(os.path.join(_CONDA_BIN, dll))
]

a = Analysis(
    [os.path.join(cfg.ROOT, 'run_ccrg.py')],
    pathex=[cfg.SRC],
    binaries=_binaries,
    datas=[
        (os.path.join(cfg.ROOT, 'src', 'ccrg'), 'ccrg'),
    ],
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx', 'dotenv',
        # 标准库，不在 ml_lib 中，必须冻结进 exe
        'sqlite3', '_sqlite3',
        # ---- ml_lib 依赖的 stdlib 子模块 ----
        # ml_lib 打包时不参与 Analysis，其 import 的 stdlib 子模块 PyInstaller 不会收集；
        # 若不显式列出，运行时 PyiFrozenImporter / PathFinder 在 _internal 找不到而报错。
        # （这是 PyInstaller 与 Nuitka 的关键差异：Nuitka 用 .py 副本 + fallback finder，
        #   PyInstaller 用 hiddenimports 显式补。）
        # urllib / http（huggingface_hub / requests / transformers）
        'urllib.error', 'urllib.parse', 'urllib.request', 'urllib.response',
        'http.client', 'http.cookiejar', 'http.cookies', 'http.server',
        # email / 编码（requests / urllib）
        'email.message', 'email.mime', 'email.mime.multipart',
        'email.mime.text', 'email.mime.base', 'email.mime.application',
        'email.parser', 'email.utils', 'email.header', 'email.charset',
        # 压缩 / 归档（huggingface_hub / transformers 模型缓存）
        'zipfile', 'gzip', 'bz2', 'lzma',
        # 元数据 / 资源
        'importlib.metadata', 'importlib.resources',
        # 原生绑定（torch / tokenizers）
        'ctypes', 'ctypes.util', 'ctypes.wintypes',
        # XML（transformers 配置解析）
        'xml.etree.ElementTree', 'xml.etree.cElementTree',
        # 网络 / 并发 / 系统
        'ssl', 'socket', 'selectors', 'queue',
        'concurrent.futures', 'subprocess', 'platform',
        'tempfile', 'shutil', 'csv', 'uuid',
        'mimetypes', 'unicodedata', 'stringprep',
        'hashlib', 'base64', 'binascii',
    ],
    hookspath=[],
    runtime_hooks=[os.path.join(_SPEC_DIR, 'runtime_ml_lib_hook.py')],
    excludes=[
        'tensorboard', 'tensorboardX',
        'matplotlib', 'PIL', 'cv2',
        'pytest', 'unittest', 'tkinter',
        # 重型 ML 栈：不冻结，改由 ml_lib 外部引用
        'torch', 'torch.cuda', 'torch.backends.cudnn',
        'sentence_transformers', 'transformers',
        'huggingface_hub', 'tokenizers', 'safetensors',
        # numpy 已从 excludes 移除：冻结进 PYZ，保证 ml_lib 缺失时启动不崩溃
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts,
    name='ccrg', debug=False, icon=None, console=True,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='ccrg',
)
