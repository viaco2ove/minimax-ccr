# claude-code-router
https://musistudio.github.io/claude-code-router/zh-CN/docs/
https://github.com/musistudio/claude-code-router
D:\Users\viaco\PycharmProjects\claude-code-router

# minimax 如何对接到claude code 和 codex

## Claude Code
```
Stpe1: 编辑或创建 Claude Code 的配置文件
       # MacOS & Linux 为 `~/.claude/settings.json`
       # Windows 为`用户目录/.claude/settings.json`
       # `MINIMAX_API_KEY` 需替换为您的 MiniMax API Key
       # 环境变量 `ANTHROPIC_AUTH_TOKEN` 和 `ANTHROPIC_BASE_URL` 优先级高于配置文件
       {
         "env": {
           "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
           "ANTHROPIC_AUTH_TOKEN": "MINIMAX_API_KEY",
           "API_TIMEOUT_MS": "3000000",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
           "ANTHROPIC_MODEL": "MiniMax-M2.7",
           "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7",
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7"
         }
       }
       # Step2: 编辑或新增 `.claude.json` 文件
       # MacOS & Linux 为 `~/.claude.json`
       # Windows 为`用户目录/.claude.json`
       # 新增 `hasCompletedOnboarding` 参数
       {
         "hasCompletedOnboarding": true
       }
```
## codex
```
[model_providers.minimax]
name = "MiniMax Chat Completions API"
base_url = "https://api.minimaxi.com/v1"
env_key = "MINIMAX_API_KEY"
wire_api = "chat"
requires_openai_auth = false
request_max_retries = 4
stream_max_retries = 10
stream_idle_timeout_ms = 300000

[profiles.m27]
model = "codex-MiniMax-M2.7"
model_provider = "minimax"`
```
## Droid
```
{
    "custom_models": [
        {
            "model_display_name": "MiniMax-M2.7",
            "model": "MiniMax-M2.7",
            "base_url": "https://api.minimaxi.com/anthropic",
            "api_key": "<MINIMAX_API_KEY>",
            "provider": "anthropic",
            "max_tokens": 64000
        }
    ]
}
```

# 问题是 minimax 似乎无法对接到 claude-code-router
直接在本项目写个伪造成deepseek协议的服务给claude-code-router 。
或者在claude-code-router 里增加 minimax 的转换器。那个更靠谱？

首先我得出一个结论 minimax 的接口是分文本接口和cli 接口的。 两个的apikey 也不一样。
然后codeplan 是不包括api 的token的

# 本项目的改为使用mmx 命令实现，让它继续走 code plan 套餐
# 文本聊天
mmx text chat --message "写一个Python爬虫"

# 文生图
mmx image "一只穿宇航服的猫" --n 3 --aspect-ratio 16:9

# 视频生成（后台异步）
mmx video generate --prompt "海浪拍打礁石" --async

# 语音合成
mmx speech synthesize --text "你好，世界" --out hello.mp3

# 音乐生成（带歌词）
mmx music generate --prompt "流行摇滚" --lyrics "[主歌]阳光照亮街道"

# 图像理解
mmx vision photo.jpg

# 网络搜索
mmx search "MiniMax AI 最新动态"

# 查配额
mmx quota

# 更新 CLI
mmx update

# 设置模型？
**可以！MMX命令行工具支持灵活配置模型，有三种核心方式，优先级清晰**：

### 一、三种配置模型的方法

#### 1. 临时指定（单条命令生效）
最常用：在具体命令后加 `--model` 参数（缩写 `-m`），覆盖默认设置：
```bash
# 文本对话指定高速版M2.7
mmx text chat --model MiniMax-M2.7-highspeed --message "写段Python爬虫"

# 流式输出+系统提示词+指定模型
mmx text chat --model MiniMax-M2.7 --system "你是资深全栈工程师" --message "设计FastAPI后端架构" --stream
```

#### 2. 全局默认配置（永久生效）
通过 `mmx config set` 设定默认模型，所有命令默认使用该配置：
```bash
# 设置文本对话默认模型
mmx config set --key default-text-model --value MiniMax-M2.7-highspeed

# 设置音乐生成默认模型
mmx config set --key default-music-model --value music-2.6

# 查看当前配置
mmx config get
```

#### 3. 环境变量（会话级临时生效）
通过环境变量覆盖默认，适合脚本自动化场景：
```bash
# Linux/macOS
export MINIMAX_MODEL="MiniMax-M2.7-highspeed"
mmx text chat --message "测试环境变量模型"

# Windows PowerShell
$env:MINIMAX_MODEL = "MiniMax-M2.7-highspeed"
mmx text chat --message "测试环境变量模型"
```

---

### 二、模型优先级规则（关键）
**`--model参数 > 环境变量 > 全局配置 > 硬编码默认值`**
- 单条命令加 `--model` 永远优先
- 无参数时看环境变量 `MINIMAX_MODEL`
- 无环境变量时用 `mmx config` 设置的默认值
- 都没有时用内置默认（文本默认 `MiniMax-M2.7`）

---

### 三、支持的常用模型（文本类）
| 模型名称 | 特点 | 适用场景 |
|---------|------|---------|
| MiniMax-M2.7 | 标准版 | 通用对话、内容创作 |
| MiniMax-M2.7-highspeed | 高速版 | 高频调用、实时响应 |
| MiniMax-M2.7-32k | 长上下文 | 文档分析、代码审计 |
| codex-MiniMax-M2.7 | 代码专用 | 编程、调试、架构设计 |

---

### 四、注意事项
1. **模型可用性**：需确保订阅/配额支持所选模型（如CodePlan用户可能只能用特定编码模型）
2. **多模态区分**：不同能力模块（文本/图像/音乐）有独立默认配置，可分别设置
3. **查看帮助**：用 `mmx text chat --help` 查看该命令支持的所有模型参数

**总结**：MMX CLI的模型配置非常灵活，既能临时指定满足单次需求，也能全局配置提升效率，完全适配你之前总结的接口隔离规则，不会与开放API的模型设置冲突。


# 配置
配置通过 .env 管理：

`  PORT=3457
  HOST=127.0.0.1
  DEFAULT_MODEL=MiniMax-M2.7
  TIMEOUT=120`

## ccr
参考配置：

[ccr.config.json](md/ccr.config.json)

# 运行
[READEME.install.md](md/READEME.install.md)

python mmx_provider.py
http://127.0.0.1:3457/v1/messages


```
  $body = @{
      model = "MiniMax-M2.7"
      messages = @(
          @{role = "user"; content = "hello"}
      )
  } | ConvertTo-Json -Depth 10

  curl -Method POST "http://127.0.0.1:3457/v1/messages" -Body $body -ContentType "application/json"
```