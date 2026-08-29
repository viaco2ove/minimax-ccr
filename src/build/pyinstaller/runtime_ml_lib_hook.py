# PyInstaller 运行时 hook
# 作用：让打包后的 exe 能从同目录下的 ml_lib/ 引用库里 import 重型 ML 依赖
# （torch / sentence_transformers 等不冻结进 exe，避免原生 dll 在 frozen 环境 segfault）。
# 该 hook 在解释器启动初期、任何应用模块 import 之前执行，因此 ml_lib 中的包
# 对后续 `import numpy` / `from sentence_transformers import ...` 可见。

import os
import sys

_exe_dir = os.path.dirname(os.path.abspath(sys.executable))
_ml_lib = os.path.join(_exe_dir, "ml_lib")

if os.path.isdir(_ml_lib) and _ml_lib not in sys.path:
    sys.path.insert(0, _ml_lib)
