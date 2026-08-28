# PyInstaller 简化打包配置
# 运行: pyinstaller ccrg.spec.sim --clean

from PyInstaller.utils.hooks import collect_all

_torch_d, _torch_b, _torch_h = collect_all('torch')
_st_d, _st_b, _st_h = collect_all('sentence_transformers')

a = Analysis(
    ['run_ccrg.py'],
    pathex=[],
    binaries=_torch_b + _st_b,
    datas=[
        ('src\\ccrg', 'ccrg'),
        ('.gateway.json', '.'),
        ('keywords.json', '.'),
    ] + _torch_d + _st_d,
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx',
        'numpy', 'sqlite3', '_sqlite3',
        'huggingface_hub', 'transformers',
    ] + _torch_h + _st_h,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tensorboard', 'tensorboardX',
        'matplotlib', 'PIL', 'cv2',
        'pytest', 'unittest',
    ],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    name='ccrg',
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='ccrg',
)
