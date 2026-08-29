"""pyinstaller.ini 配置加载器：供 src/build/pyinstaller 下各构建脚本共用。

- 配置项缺失时回退默认值
- 路径支持绝对路径，也支持相对 ROOT 的相对路径
- 通过 `import pyinstaller_cfg` 使用全局 `cfg` 实例
"""
import os

_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyinstaller.ini")

_DEFAULTS = {
    "ROOT": "",                    # 项目根目录
    "DIST": "/dist_py/",           # 产物父目录（相对 ROOT），下含 ccrg/
    "conda_path": "",              # conda 安装根目录（取运行时 DLL）
    "conda_envs": "",              # conda 环境根目录（python3.dll 来源）
    "conda_python_ver": "python=3.12",
    "conda_ver": "",
    "USERPROFILE": "",
    "port": "2048",                # 打包产物固定监听端口
    "py_exe": "",                  # PyInstaller 打包用 python.exe（含项目依赖 + PyInstaller）
    "site_packages": "",           # ML 栈所在 site-packages（ml_lib 来源）；缺省取 conda_envs
    "DIST_UPDATE": "/dist_py/ccrg_update",
}


def load_ini(path=None):
    cfg = dict(_DEFAULTS)
    path = path or _INI
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return cfg


def _abs(root, p):
    p = os.path.expanduser(p or "")
    if not p:
        return ""
    drive, _ = os.path.splitdrive(p)
    if drive:
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(root, p.lstrip("/\\")))


class PyInstallerCfg:
    def __init__(self, path=None):
        self.raw = load_ini(path)

        # 项目根
        self.ROOT = os.path.normpath(self.raw.get("ROOT", "") or os.getcwd())

        # 产物目录
        dist_base = _abs(self.ROOT, self.raw.get("DIST", "/dist_py/"))
        self.DIST_BASE = dist_base
        self.DIST = os.path.join(dist_base, "ccrg")
        self.ML_LIB = os.path.join(self.DIST, "ml_lib")
        self.BUILD_TEMP = os.path.join(dist_base, "ccrg_build_temp")
        self.DIST_UPDATE = _abs(self.ROOT, self.raw.get("DIST_UPDATE", "/dist_py/ccrg_update"))

        # Python / conda
        self.CONDA_ENVS = self.raw.get("conda_envs", "")
        self.CONDA_PATH = self.raw.get("conda_path", "")
        self.CONDA_VER = self.raw.get("conda_ver", "")
        self.CONDA_PYTHON_VER = self.raw.get("conda_python_ver", "python=3.12")
        self.USERPROFILE = self.raw.get("USERPROFILE", "") or os.environ.get("USERPROFILE", "")
        self.PORT = int(self.raw.get("port", "2048") or 2048)

        # PyInstaller 打包用 python.exe：优先 py_exe，回退 conda_envs
        py_exe = os.path.expanduser(self.raw.get("py_exe", "") or "")
        self.PYTHON_EXE = py_exe or (os.path.join(self.CONDA_ENVS, "python.exe") if self.CONDA_ENVS else "")

        # ML 栈 site-packages（ml_lib 来源）：优先显式 site_packages，回退 conda_envs/Lib/site-packages
        sp = os.path.expanduser(self.raw.get("site_packages", "") or "")
        if not sp and self.CONDA_ENVS:
            sp = os.path.join(self.CONDA_ENVS, "Lib", "site-packages")
        self.SITE_PACKAGES = sp

        # 项目内关键路径
        self.SRC = os.path.join(self.ROOT, "src")
        self.ENTRY = os.path.join(self.ROOT, "run_ccrg.py")
        self.GATEWAY = os.path.join(self.ROOT, ".gateway.json")
        self.KEYWORDS = os.path.join(self.ROOT, "keywords.json")

        # 本脚本所在目录（src/build/pyinstaller）
        self.BUILD_DIR = os.path.dirname(os.path.abspath(__file__))

    def require_env(self):
        """校验并返回打包用 python.exe，缺失时给出明确错误。"""
        if not os.path.isfile(self.PYTHON_EXE):
            raise RuntimeError(
                f"python.exe not found: {self.PYTHON_EXE}\n"
                f"请检查 pyinstaller.ini 中的 py_exe / conda_envs 配置。")
        return self.PYTHON_EXE


cfg = PyInstallerCfg()
