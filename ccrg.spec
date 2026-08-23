# PyInstaller 打包配置
# 运行: pyinstaller ccrg.spec

from PyInstaller.utils.hooks import collect_all

# torch 需要 collect_all 才能正确打包（原生 DLL + 子模块多）
_torch_datas, _torch_binaries, _torch_hiddenimports = collect_all('torch')
_st_datas, _st_binaries, _st_hiddenimports = collect_all('sentence_transformers')

a = Analysis(
    ['run_ccrg.py'],
    pathex=[],
    binaries=_torch_binaries + _st_binaries,
    datas=[
        ('src\\ccrg', 'ccrg'),
        ('.gateway.json', '.'),
        ('keywords.json', '.'),
    ] + _torch_datas + _st_datas,
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx', 'python_dotenv',
        'numpy',
        'sqlite3', '_sqlite3',
        'torch', 'sentence_transformers',
        'huggingface_hub', 'transformers',
    ] + _torch_hiddenimports + _st_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tensorboard', 'tensorboardX',
        'matplotlib', 'PIL', 'cv2',
        'pytest', 'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='ccrg',
    debug=False,
    icon=None,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ccrg',
)
