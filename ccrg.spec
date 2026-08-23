# PyInstaller 打包配置
# 运行: pyinstaller ccrg.spec

from PyInstaller.utils.hooks import collect_all

_torch_d, _torch_b, _torch_h = collect_all('torch')
_st_d, _st_b, _st_h = collect_all('sentence_transformers')
_tr_d, _tr_b, _tr_h = collect_all('transformers')
_tok_d, _tok_b, _tok_h = collect_all('tokenizers')
_safe_d, _safe_b, _safe_h = collect_all('safetensors')
_hf_hub_d, _hf_hub_b, _hf_hub_h = collect_all('huggingface_hub')

a = Analysis(
    ['run_ccrg.py'],
    pathex=[],
    binaries=_torch_b + _st_b + _tr_b + _tok_b + _safe_b + _hf_hub_b,
    datas=[
        ('src\\ccrg', 'ccrg'),
        ('.gateway.json', '.'),
        ('keywords.json', '.'),
    ] + _torch_d + _st_d + _tr_d + _tok_d + _safe_d + _hf_hub_d,
    hiddenimports=[
        'fastapi', 'uvicorn', 'httpx', 'python_dotenv',
        'numpy', 'sqlite3', '_sqlite3',
        'torch', 'torch.cuda', 'torch.backends.cudnn',
        'sentence_transformers', 'transformers',
        'huggingface_hub', 'tokenizers', 'safetensors',
    ] + _torch_h + _st_h + _tr_h + _tok_h + _safe_h + _hf_hub_h,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tensorboard', 'tensorboardX',
        'matplotlib', 'PIL', 'cv2',
        'pytest', 'unittest', 'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='ccrg', debug=False, icon=None, console=True,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name='ccrg',
)
