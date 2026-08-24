# CCRG 打包构建与使用指南

## 目录

- [概述](#概述)
- [前置条件](#前置条件)
- [生成 EXE](#生成-exe)
- [产物结构说明](#产物结构说明)
- [部署与运行](#部署与运行)
- [配置修改](#配置修改)
- [日志与监控](#日志与监控)
- [常见问题](#常见问题)

---

## 概述

CCRG（Claude Code Router Gateway）采用 **EXE + 外部 ML 库** 的打包架构：

```
dist/ccrg/                   ← 整体可分发目录（约 737MB）
├── ccrg.exe                 ← 主程序（约 9MB）
├── _internal/               ← Python 运行时 + 应用依赖（约 29MB）
├── ml_lib/                  ← ML 引用库（约 700MB，可选）
│   ├── torch/
│   ├── sentence_transformers/
│   ├── transformers/
│   └── ...
├── .gateway.json            ← 路由/Provider 配置
├── keywords.json            ← 关键词路由规则
└── logs/                    ← 运行日志目录
```

**设计思路**：
- **exe + _internal**：核心程序，包含 Python 解释器、FastAPI、httpx 等轻量依赖
- **ml_lib/**：可选的 ML 栈（torch + sentence_transformers），用于语义分割路由。缺失时自动降级为关键词路由，不影响基本功能
- **.gateway.json**：外置配置文件，可独立修改无需重新打包

---

## 前置条件

### 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.11+（含虚拟环境 `.venv`） |
| PyInstaller | 6.x（脚本会自动安装） |
| 操作系统 | Windows 10/11 64位 |
| 磁盘空间 | 构建约需 2GB（含 ML 栈） |

### 目录结构（构建前）

```
minimax-ccr/
├── .venv/                   ← 虚拟环境（必须存在）
├── src/ccrg/                ← 源代码
├── ccrg.spec                ← PyInstaller 打包规格
├── build_copy_ml_lib.py     ← ML 库拷贝脚本
├── runtime_ml_lib_hook.py   ← 运行时路径注入钩子
├── .gateway.json            ← 网关配置
├── keywords.json            ← 关键词规则
├── run_ccrg.py              ← 入口脚本
└── md/run_install/
    ├── bulid_exe.bat        ← 一键打包脚本
    └── exe_use.md           ← 本文档
```

---

## 生成 EXE

### 方式一：一键打包（推荐）

双击运行：

```
md\run_install\bulid_exe.bat
```

或在项目根目录的 CMD 中执行：

```cmd
md\run_install\bulid_exe.bat
```

脚本会自动完成以下 5 个步骤：

| 步骤 | 操作 | 说明 |
|------|------|------|
| **1/5** | 环境准备 | 激活 `.venv`，检查 PyInstaller |
| **2/5** | 清理旧构建 | 删除 `build/` 和 `dist/ccrg/`（被占用时跳过） |
| **3/5** | PyInstaller 构建 | 根据 `ccrg.spec` 打包 exe（约 2-5 分钟） |
| **4/5** | 拷贝 ML 库 | 从 `.venv` 复制 torch 等到 `dist/ccrg/ml_lib/` |
| **5/5** | 验证产物 | 检查 exe、配置、目录完整性 |

> **注意**：步骤 4 失败不会中断构建。exe 仍可正常运行，只是语义路由不可用（自动降级为关键词路由）。

### 方式二：手动分步执行

如需更细粒度的控制，可逐步执行：

```cmd
:: 1. 激活虚拟环境
call .venv\Scripts\activate.bat

:: 2. 清理旧构建（可选）
rd /s /q build 2>nul
rd /s /q dist\ccrg 2>nul

:: 3. 禁用 WorkBuddy 沙箱钩子（避免文件操作被拦截）
set CODEBUDDY_SESSION_ID=
set CLAUDE_SESSION_ID=

:: 4. PyInstaller 构建
pyinstaller ccrg.spec --noconfirm

:: 5. 拷贝 ML 库（可选）
python build_copy_ml_lib.py
```

### 构建产物位置

```
dist/ccrg/        ← 完整可分发目录（推荐）
dist/ccrg.exe     ← 单文件版本（不推荐，无法直接运行）
```

---

## 产物结构说明

### dist/ccrg/ 目录

| 文件/目录 | 大小 | 说明 |
|-----------|------|------|
| `ccrg.exe` | ~9 MB | 主程序入口 |
| `_internal/` | ~29 MB | Python 运行时 + FastAPI/httpx 等核心依赖 |
| `ml_lib/` | ~700 MB | ML 引用库（torch/sentence_transformers），**可选** |
| `.gateway.json` | ~10 KB | 路由配置（Provider、端口、规则） |
| `keywords.json` | ~3 KB | 关键词路由规则 |
| `logs/` | - | 运行时日志目录（自动创建） |

### ml_lib/ 内容

```
ml_lib/
├── torch/                   PyTorch（CPU 版本）
├── sentence_transformers/   语义向量模型库
├── transformers/            Hugging Face 基础库
├── numpy/                   数值计算
├── safetensors/             模型权重加载
├── tokenizers/              分词器
└── huggingface_hub/         模型下载管理
```

> 如果不需要语义路由功能，可以删除 `ml_lib/` 整个目录，节省约 700MB 空间。

---

## 部署与运行

### 基本运行

**方式一：双击运行**

直接双击 `dist\ccrg\ccrg.exe`

**方式二：命令行运行**

```cmd
cd dist\ccrg
ccrg.exe
```

启动成功后会看到：

```
INFO:     Started server process [xxxxx]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:3428 (Press CTRL+C to quit)
```

### 验证服务

```cmd
:: 检查健康状态
curl http://127.0.0.1:3428/health

:: 查看统计面板（浏览器）
http://127.0.0.1:3428/stats
```

### 端口修改

编辑 `dist\ccrg\.gateway.json` 中的 `server.port` 字段：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 3428,        ← 修改此值
    ...
  }
}
```

修改后重启 exe 生效。

### 分发到其他机器

将整个 `dist\ccrg\` 目录打包发送即可：

```cmd
:: 打包为 zip
cd dist
tar -acf ccrg.zip ccrg/
```

接收方解压后直接运行 `ccrg.exe`，**无需安装 Python 或任何依赖**。

> **注意**：接收方电脑需满足以下条件：
> - Windows 10/11 64位
> - 如需语义路由：需有足够内存加载模型（建议 8GB+ RAM）

---

## 配置修改

### 配置文件：.gateway.json

所有配置均在 `.gateway.json` 中，**修改后重启 exe 即可生效，无需重新打包**。

#### Provider 配置示例

```json
{
  "providers": {
    "minimax": {
      "api_base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "your-api-key-here",
      "protocol": "codeplan_anthropic",
      "models": ["MiniMax-M2.7"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": true,
        "vision": true,
        "max_context": 122000
      }
    }
  }
}
```

#### 路由规则配置

```json
{
  "routing": {
    "default": "minimax:MiniMax-M2.7",
    "scenarios": {
      "think": {
        "route": "xiaomi:mimo-v2.5-pro",
        "fallback": ["minimax:MiniMax-M2.7"]
      }
    },
    "tool_routing": {
      "cheap_tasks": {
        "match": ["Read", "Glob", "Grep"],
        "match_mode": "any",
        "route": "minimax:MiniMax-M2.7"
      }
    }
  }
}
```

#### 关键词规则配置

编辑 `keywords.json` 添加自定义关键词路由规则。

---

## 日志与监控

### 日志文件

```
dist/ccrg/logs/ccrg.log          ← 当前日志
dist/ccrg/logs/ccrg.log.2026-08-24  ← 历史日志（按天滚动）
```

### 控制台面板

浏览器访问：http://127.0.0.1:3428/stats

查看内容：
- 当前路由统计
- Provider 请求计数
- Token 使用量
- 错误率

### 日志级别

在 `.gateway.json` 中调整：

```json
{
  "server": {
    "log_level": "debug"    ← debug / info / warning / error
  }
}
```

---

## 常见问题

### Q1: 启动后立即闪退

**原因**：配置文件缺失或格式错误

**解决**：确认 `dist\ccrg\` 目录下存在 `.gateway.json` 和 `keywords.json`。可在命令行运行 exe 查看错误输出：

```cmd
cd dist\ccrg
ccrg.exe
:: 观察输出的错误信息
pause
```

### Q2: 访问 API 时崩溃

**原因**：ML 库加载失败导致段错误

**解决**：
1. 如果不需要语义路由，直接删除 `dist\ccrg\ml_lib\` 目录
2. 如需语义路由，确保 `ml_lib/` 目录完整（重新运行 `build_copy_ml_lib.py`）

### Q3: 构建时 PyInstaller 报文件占用

**原因**：之前的 exe 进程未完全退出

**解决**：
```cmd
:: 1. 结束残留进程
taskkill /f /im ccrg.exe 2>nul

:: 2. 等待一秒
timeout /t 1

:: 3. 重新运行打包脚本
md\run_install\bulid_exe.bat
```

### Q4: 如何只打包轻量版（不含 ML 库）

跳过构建脚本的第 4 步，或在构建后手动删除：

```cmd
rd /s /q dist\ccrg\ml_lib
```

exe 仍可正常运行，自动使用关键词路由。

### Q5: 构建后修改了代码，需要重新打包

直接重新运行打包脚本：

```cmd
md\run_install\bulid_exe.bat
```

脚本会自动清理旧构建并重新生成。

### Q6: 端口 3428 被占用

修改 `.gateway.json` 中的端口：

```json
{
  "server": {
    "port": 3429    ← 改为其他端口
  }
}
```

或先释放端口：

```cmd
:: 查找占用端口的进程
netstat -ano | findstr :3428

:: 结束进程（替换 PID）
taskkill /f /pid <PID>
```

### Q7: 运行时提示缺少 DLL

**原因**：Windows 系统缺少 Visual C++ 运行时

**解决**：安装 [Microsoft Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

---

## 架构说明（可选阅读）

### 为什么采用 exe + 外部库？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **单文件 exe** | 分发简单 | 体积大（~740MB），启动慢（需解压），崩溃风险高 |
| **exe + 外部库（当前）** | 启动快，配置灵活，ML 库可选 | 需要目录结构 |

### 运行时加载流程

```
ccrg.exe 启动
  │
  ├─ 运行 runtime_ml_lib_hook.py（钩子）
  │    └─ 将 ml_lib/ 插入 sys.path[0]
  │
  ├─ 加载 .gateway.json（配置）
  │
  └─ 启动 FastAPI 服务
       │
       └─ 首次请求触发语义分割器
            ├─ ml_lib 存在 → 加载 sentence_transformers → 语义路由
            └─ ml_lib 缺失 → _load_failed=True → 关键词路由
```

### 安全提示

> **⚠️ 重要**：`.gateway.json` 中包含 API Key，分发时请注意脱敏。
>
> 快速脱敏方法：
> ```cmd
> :: 替换 api_key 值为占位符
> powershell -Command "(Get-Content .gateway.json) -replace '\"api_key\": \"[^\"]*\"', '\"api_key\": \"YOUR_KEY_HERE\"' | Set-Content .gateway.json"
> ```
