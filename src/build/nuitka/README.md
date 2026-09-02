---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: ce997c05230104345033aec2d643180c_0fee15b7a37211f192a2525400287e28
    ReservedCode1: FjeXpE+C1cCSd4q0AnnIQRqG8rsENwqXgNpO5q8xV18+ky7Xyi8RJWY00n19mCXxNG0BwxmfD3ENZgMPgrqsbveHj1f/h27E59QrRcujTKznYKxmOL9foAI9JItX7uLPS7+46MzpdiTMM2hYB2NZKj22yBz8UW3ViQF/ZhjmUWxw2rPx9fcHnNmUtwE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: ce997c05230104345033aec2d643180c_0fee15b7a37211f192a2525400287e28
    ReservedCode2: FjeXpE+C1cCSd4q0AnnIQRqG8rsENwqXgNpO5q8xV18+ky7Xyi8RJWY00n19mCXxNG0BwxmfD3ENZgMPgrqsbveHj1f/h27E59QrRcujTKznYKxmOL9foAI9JItX7uLPS7+46MzpdiTMM2hYB2NZKj22yBz8UW3ViQF/ZhjmUWxw2rPx9fcHnNmUtwE=
---

# minimax-ccr Nuitka 打包与运行说明

Claude Code Router Gateway 的 Windows 独立 exe 打包流程。
所有脚本集中在 `src/build/nuitka/`，统一从同目录 `nuitka.ini` 读取配置（路径、环境、端口均不硬编码）。

## 目录结构

```
src/build/nuitka/
├── nuitka.ini               # 配置文件（唯一需要按机器修改的文件）
├── nuitka_cfg.py            # 配置加载器（供各脚本共用，一般无需改动）
├── build_nuitka.py          # 打包主脚本：编译 + 拷配置/DLL/ML库 + 自动后处理
├── build_copy_ml_nu_lib.py  # 把重型 ML 栈(torch/transformers/...)拷进 dist/ml_lib/
├── supply_ml_deps.py        # 按 Requires-Dist 闭包补齐 ml_lib 缺失依赖（如 narwhals）
└── post_build_fix.py        # 编译后必备后处理（见下文「后处理是什么」）
```

## 配置文件 nuitka.ini

| 键 | 说明 | 示例 |
|----|------|------|
| `ROOT` | 项目根目录 | `$root` |
| `DIST` | 产物父目录（相对 ROOT，下含 `ccrg/`） | `/dist_nu/` |
| `conda_path` | conda 安装根（用于取运行时 DLL） | `C:\ProgramData\miniconda3` |
| `conda_envs` | 打包用 Python 环境（必须是 Python 3.12） | `C:\Users\viaco\.conda\envs\ccrg312` |
| `conda_python_ver` | 创建环境时的 Python 版本号 | `python=3.12` |
| `conda_ver` | conda 发行版名（仅信息用） | `miniconda3` |
| `USERPROFILE` | 当前用户主目录 | `C:\Users\viaco` |
| `port` | 打包产物固定监听端口（可选，默认 2048） | `2048` |

说明：
- 所有脚本通过 `nuitka_cfg.py` 读取此文件；路径支持绝对路径，也支持相对 `ROOT` 的相对路径。
- 换机器只需改 `ROOT`、`conda_path`、`conda_envs`、`USERPROFILE` 四项。
- `DIST` 以 `/` 开头表示相对 `ROOT`；最终产物位于 `DIST/ccrg/`。

## 打包

一条命令完成（编译 + 拷配置/DLL/ML 库 + 后处理）：
例如： conda_envs==C:\Users\xxx\.conda\envs\ccrg312
```PowerShell
# 设置变量（当前会话生效）
$conda_envs = "{conda_envs}"
# 使用变量执行
& $conda_envs/python.exe src\build\nuitka\build_nuitka.py
```

```cmd
:: 设置变量（当前会话生效）
set conda_envs={onda_envs}

:: 使用变量执行
%conda_envs%\python.exe src\build\nuitka\build_nuitka.py

```

### 新增 --update 参数。
打包 + 后处理完成后，自动把完整 dist 复制到 dist_nu/ccrg_update，
并剔除所有运行时数据——.gateway.json、keywords.json、logs/、faulthandler.log、*.db
build_nuitka.py --update
%conda_envs%\python.exe src\build\nuitka\build_nuitka.py --update

### 或先 `cd` 到项目根再运行：

```
cd $root
$conda_envs\python.exe src\build\nuitka\build_nuitka.py
```

流程：
1. 检查/安装 `nuitka`
2. 清理旧 `dist_nu/`
3. Nuitka 编译 `run_ccrg.py`（约 5-15 分钟）
4. 重命名产物到 `dist_nu/ccrg/`
5. 拷贝 `.gateway.json`、`keywords.json`、运行时 DLL
6. 拷贝 ML 库到 `dist_nu/ccrg/ml_lib/`
7. 自动执行后处理（见下）

产物目录：`dist_nu/ccrg/`，入口为 `run_ccrg.exe`。

## 后处理是什么（post_build_fix.py）

Nuitka 打包完成后，exe 要能加载外部 `ml_lib/` 里的 torch/transformers 等，还差四件事，由 `post_build_fix.py` 统一处理：

1. **补纯 stdlib 副本**：入口脚本里加了磁盘兜底 Finder，它需要 `dist/` 根目录有完整的纯 `.py` stdlib 副本（从 `conda_envs/Lib` 拷贝，排除 `site-packages`/`__pycache__`/`ml_lib`），否则 `nuitka_module_loader` 拦截 stdlib 子模块会导致 torch 等加载失败、模型降级为关键词路由。
2. **补 `python3.dll`**：`tokenizers` 的 DLL 依赖，缺失则 import 报错。
3. **补缺失依赖**：调用 `supply_ml_deps.py`，按 `Requires-Dist` 依赖闭包把 `ml_lib` 缺的纯 Python 依赖（如 `narwhals`）补齐。
4. **固定端口**：把 `dist/.gateway.json` 的 `server.port` 写为 `nuitka.ini` 的 `port`（默认 2048），防止端口漂移/被占用冲突。

## 运行

```
cd dist_nu\ccrg
run_ccrg.exe
```

- 常驻监听 `2048` 端口（以 `nuitka.ini` 的 `port` 为准）。
- 模型正常加载时为语义路由（`SentenceTransformer`）；若 `dist` 缺 stdlib 副本或 `ml_lib` 不完整，会降级为关键词路由，请按上文后处理补全。

## 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| exe 启动即退出 / 找不到模块 | 未跑后处理或 `ml_lib` 不全 | 重跑 `build_nuitka.py`（会自动后处理） |
| 端口被占用 | 上一次实例未退出 | 结束旧 `run_ccrg.exe` 进程，或改 `nuitka.ini` 的 `port` 后重打包 |
| 模型降级为关键词路由 | `dist` 缺 stdlib 副本 | 确认后处理第 1 步已执行 |
| 打包环境提示 python.exe 不存在 | `conda_envs` 配错 | 修正 `nuitka.ini` 的 `conda_envs` |
*（内容由AI生成，仅供参考）*
