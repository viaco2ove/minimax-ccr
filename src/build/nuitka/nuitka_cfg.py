"""nuitka.ini 配置加载器：供 src/build/nuitka 下各构建脚本共用。

- 配置项缺失时回退默认值
- 路径支持绝对路径，也支持相对 ROOT 的相对路径
- 通过 `import nuitka_cfg` 使用全局 `cfg` 实例
"""
import os

_INI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nuitka.ini")

_DEFAULTS = {
    "ROOT": "",                    # 项目根目录
    "DIST": "/dist_nu/",           # 产物父目录（相对 ROOT），下含 ccrg/
    "conda_path": "",              # conda 安装根目录
    "conda_envs": "",              # 打包用 Python 环境（如 C:/Users/xx/.conda/envs/ccrg312）
    "conda_python_ver": "python=3.12",
    "conda_ver": "",
    "USERPROFILE": "",
    "port": "2048",                # 打包产物固定监听端口
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
    if drive:  # 盘符形式(C:/...)或 UNC 视为绝对；裸 /dist_nu/ 按相对 ROOT 处理
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(root, p.lstrip("/\\")))


class NuitkaCfg:
    def __init__(self, path=None):
        self.raw = load_ini(path)

        # 项目根
        self.ROOT = os.path.normpath(self.raw.get("ROOT", "") or os.getcwd())

        # 产物目录
        dist_base = _abs(self.ROOT, self.raw.get("DIST", "/dist_nu/"))
        self.DIST_BASE = dist_base                    # <ROOT>/dist_nu
        self.DIST = os.path.join(dist_base, "ccrg")   # <ROOT>/dist_nu/ccrg
        self.ML_LIB = os.path.join(self.DIST, "ml_lib")
        self.BUILD_TEMP = os.path.join(dist_base, "ccrg_build_temp")

        # Python / conda
        self.CONDA_ENVS = self.raw.get("conda_envs", "")
        self.CONDA_PATH = self.raw.get("conda_path", "")
        self.CONDA_VER = self.raw.get("conda_ver", "")
        self.CONDA_PYTHON_VER = self.raw.get("conda_python_ver", "python=3.12")
        self.USERPROFILE = self.raw.get("USERPROFILE", "") or os.environ.get("USERPROFILE", "")
        self.PORT = int(self.raw.get("port", "2048") or 2048)

        self.PYTHON_EXE = os.path.join(self.CONDA_ENVS, "python.exe") if self.CONDA_ENVS else ""
        self.LIB_DIR = os.path.join(self.CONDA_ENVS, "Lib") if self.CONDA_ENVS else ""
        self.SITE_PACKAGES = os.path.join(self.LIB_DIR, "site-packages") if self.LIB_DIR else ""

        # 项目内关键路径
        self.SRC = os.path.join(self.ROOT, "src")
        self.ENTRY = os.path.join(self.ROOT, "run_ccrg.py")
        self.GATEWAY = os.path.join(self.ROOT, ".gateway.json")
        self.KEYWORDS = os.path.join(self.ROOT, "keywords.json")

        # 本脚本所在目录（src/build/nuitka）
        self.BUILD_DIR = os.path.dirname(os.path.abspath(__file__))

    def require_env(self):
        """校验并返回打包用 python.exe，缺失时给出明确错误。"""
        if not os.path.isfile(self.PYTHON_EXE):
            raise RuntimeError(
                f"python.exe not found: {self.PYTHON_EXE}\n"
                f"请检查 nuitka.ini 中的 conda_envs 配置。")
        return self.PYTHON_EXE


cfg = NuitkaCfg()
