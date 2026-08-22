# PyInstaller 打包配置
# 运行: pyinstaller ccrg.spec

a = Analysis(
    ['run_ccrg.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('src\\ccrg', 'ccrg'),
        ('.gateway.json', '.'),
        ('keywords.json', '.'),
    ],
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx', 'python_dotenv',
        'numpy',
        'sqlite3', '_sqlite3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio',
        'sentence_transformers',
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
