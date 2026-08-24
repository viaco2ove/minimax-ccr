# PyInstaller 打包配置 (onedir + 外部 ml_lib 引用库)
# 运行: pyinstaller ccrg.spec
#
# 设计：重型 ML 栈(torch / sentence_transformers / transformers / tokenizers /
# safetensors / huggingface_hub / numpy) 不冻结进 exe，而是作为真实包放在
# dist/ccrg/ml_lib/，运行时由 runtime_ml_lib_hook.py 注入 sys.path 后正常 import。
# 这样：
#   1. exe 体积小、启动快，且避免 PyInstaller 搬运 torch 原生 dll 导致的 segfault；
#   2. ml_lib 缺失时 exe 仍正常运行(自动降级 keyword 路由，不崩溃)；
#   3. 放入 ml_lib 即启用语义路由，等于"装插件升级"。
# 构建后需运行 build_copy_ml_lib.py 把 ML 栈拷进 dist/ccrg/ml_lib/。

a = Analysis(
    ['run_ccrg.py'],
    pathex=[],
    binaries=[
        # conda 环境中的系统 DLL，PyInstaller 无法自动解析
        ('D:/ProgramData/miniconda3/Library/bin/sqlite3.dll', '.'),
        ('D:/ProgramData/miniconda3/Library/bin/ffi.dll', '.'),
        ('D:/ProgramData/miniconda3/Library/bin/liblzma.dll', '.'),
        ('D:/ProgramData/miniconda3/Library/bin/libbz2.dll', '.'),
        ('D:/ProgramData/miniconda3/Library/bin/libmpdec-4.dll', '.'),
        ('D:/ProgramData/miniconda3/Library/bin/libexpat.dll', '.'),
    ],
    datas=[
        ('src\\ccrg', 'ccrg'),
        ('.gateway.json', '.'),
        ('keywords.json', '.'),
    ],
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx', 'dotenv',
        # 标准库，不在 ml_lib 中，必须冻结进 exe
        'sqlite3', '_sqlite3',
    ],
    hookspath=[],
    runtime_hooks=['runtime_ml_lib_hook.py'],
    excludes=[
        'tensorboard', 'tensorboardX',
        'matplotlib', 'PIL', 'cv2',
        'pytest', 'unittest', 'tkinter',
        # 重型 ML 栈：不冻结，改由 ml_lib 外部引用
        'torch', 'torch.cuda', 'torch.backends.cudnn',
        'sentence_transformers', 'transformers',
        'huggingface_hub', 'tokenizers', 'safetensors',
        'numpy',
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
