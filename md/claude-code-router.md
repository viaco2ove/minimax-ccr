# Claude Code Router 与 MiniMax 对接分析

## Claude Code Router 工作原理

### 核心架构

Claude Code Router (CCR) 是一个基于 Fastify 的本地代理服务器，监听默认端口 3456。它拦截 Claude Code 发出的 Anthropic 格式 API 请求，通过 Transform Pipeline 转换为目标 Provider 的格式后转发。

**请求流程：**

```
Claude Code CLI
      │
      ▼ (ANTHROPIC_BASE_URL=http://127.0.0.1:3456)
CCR Proxy Server (Fastify)
      │
      ├─ ConfigService: 加载 ~/.claude-code-router/config.json
      ├─ TransformerService: 选择并应用 transformers
      ├─ ProviderService: 解析 provider 路由
      │
      ▼
Transformer Pipeline:
  1. transformRequestOut (Anthropic → Provider 格式)
  2. Provider-level transformers
  3. Model-specific transformers
      │
      ▼
Target Provider API
      │
      ▼
Response Pipeline (反向):
  1. Model-specific transformers
  2. Provider-level transformers
  3. transformResponseIn → 转回 Anthropic 格式
      │
      ▼
Claude Code CLI
```

### 路由机制

CCR 使用**场景路由**（Scenario Routing）将请求分发到不同模型：

| Route Key | 用途 | 典型模型策略 |
|---|---|---|
| `default` | 通用任务 | 高质量模型 |
| `background` | 文件分析、索引等后台任务 | 廉价/快速模型 |
| `think` | 推理密集型任务（Plan Mode） | 最强模型 |
| `longContext` | 上下文超过阈值（默认 60000 tokens） | 大上下文窗口模型 |
| `webSearch` | 网页搜索任务 | 原生搜索模型 |
| `image` | 图片相关任务 | 视觉模型 |

路由字符串格式：`"provider_name,model_name"`（中间是逗号）

---

## MiniMax 与 Claude Code 对接现状

### MiniMax 的 Anthropic 兼容端点

| 区域 | 端点 |
|---|---|
| 国际版 | `https://api.minimax.io/anthropic` |
| 中国版 | `https://api.minimaxi.com/anthropic` |

### 直接配置 Claude Code（有效）

在 `settings.json` 中配置：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "MINIMAX_API_KEY",
    "ANTHROPIC_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
```

这种方式**可以正常工作**，MiniMax-M2 模型在直接对接 Claude Code 时表现良好。

### 通过 CCR 配置（存在问题）

GitHub Issue [#964](https://github.com/musistudio/claude-code-router/issues/964) 明确报告了此问题：

> "Unable to configure Minimax M2 in router but works perfect direct in Claude code!"

用户在 CCR 中配置 MiniMax-M2 无法正常工作，但直接配置 Claude Code 则完全正常。

---

## 问题根因分析

### 1. 双转换问题（Double Conversion）

GitHub Issue [#1317](https://github.com/musistudio/claude-code-router/issues/1317) 指出了 CCR 的一个核心设计问题：

> "Anthropic-native providers get double-converted by OpenAI→Anthropic transformer"

**问题机制：**

1. CCR 默认假设所有 Provider 都是 **OpenAI 兼容**格式
2. Claude Code 发出的是 **Anthropic Messages API** 格式
3. 所以 CCR 默认执行 **Anthropic → OpenAI** 的转换（transformRequestOut）
4. 但 MiniMax 本身已经是 **Anthropic 兼容**格式，不需要转换
5. 如果错误地使用了 `openai` transformer，MiniMax 收到的会是**转换后的 OpenAI 格式**而非原始 Anthropic 格式

### 2. MiniMax API 的限制

即使正确对接，MiniMax 的 Anthropic 兼容端点也存在以下限制：

| 功能 | 状态 | 说明 |
|---|---|---|
| 文本对话 | ✅ 正常 | 标准 messages.create() |
| Tool Use | ✅ 正常 | 结构化工具定义和结果 |
| Streaming | ✅ 正常 | 支持 |
| Interleaved Thinking | ✅ 正常 | Mini-Agent 框架支持 |
| 图片/文档输入 | ❌ 不支持 | 仅支持文本和工具调用 |
| Thinking Budget | ⚠️ 忽略 | 参数被静默忽略 |
| MCP Servers | ⚠️ 忽略 | 参数被静默忽略 |
| 重复 Tool Result | ❌ 报错 | Error 2013: `tool call and result not match` |

**关键错误码：** 当多个 tool calls 共享相同内容时，MiniMax 返回 `2013` 错误。

### 3. 配置字段理解偏差

CCR Provider 配置中的 `api_base_url` 字段需要指向**完整的 chat completions 端点**，但 MiniMax 的 Anthropic 端点是 `/v1/messages` 格式，与标准的 `/v1/chat/completions` 不同。如果配置时路径错误，可能导致 404。

---

## 解决方案

### 方案一：正确配置 CCR Provider（推荐）

在 `~/.claude-code-router/config.json` 中：

```json
{
  "Providers": [
    {
      "name": "minimax",
      "api_base_url": "https://api.minimax.io/anthropic/v1/messages",
      "api_key": "$MINIMAX_API_KEY",
      "models": ["MiniMax-M2", "MiniMax-M2.7"],
      "transformer": {
        "use": ["anthropic"]
      }
    }
  ],
  "Router": {
    "default": "minimax,MiniMax-M2",
    "background": "minimax,MiniMax-M2",
    "think": "minimax,MiniMax-M2"
  }
}
```

**关键点：**
- `api_base_url` 必须包含完整的 `/v1/messages` 路径
- `transformer.use` 必须指定 `"anthropic"`，避免 OpenAI 转换
- 不需要额外的 tooluse 或 maxtoken transformer（除非有特殊需求）

### 方案二：使用 claude-code-mux 替代（Rust 实现）

[claude-code-mux](https://github.com/9j/claude-code-mux) 是 Rust 实现的高性能路由代理，已将 MiniMax 作为**第一类 Anthropic 兼容 Provider** 支持：

1. 添加 Provider，类型选择 **Minimax**
2. 添加模型映射：`minimax-m2` → provider `minimax`，actual model `MiniMax M2`
3. 在 Router 中设置默认模型
4. 使用 **Auto-map Regex** 模式 `^claude-` 自动将 Claude 模型请求转换为 MiniMax 模型

### 方案三：避免使用 CCR，直接配置 Claude Code

如果 CCR 配置复杂且不稳定，可以在 `settings.json` 中直接配置 MiniMax：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "你的MINIMAX_API_KEY",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "ANTHROPIC_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2"
  }
}
```

这种方式绕过了 CCR，适合不需要多模型路由的场景。

---

## 总结

| 问题 | 原因 | 解决 |
|---|---|---|
| CCR 中 MiniMax 无法工作 | 双转换问题 + transformer 配置错误 | 使用 `anthropic` transformer + 正确 `api_base_url` |
| 工具调用报错 2013 | MiniMax 对重复 tool result 校验更严格 | 确保每个 tool call 结果唯一 |
| 图片无法处理 | MiniMax Anthropic 端点阉割了视觉功能 | 使用 OpenAI 兼容端点或等待支持 |
| 404 错误 | `api_base_url` 路径不完整 | 添加 `/v1/messages` 后缀 |

---

## 参考资料

- [Claude Code Router GitHub](https://github.com/musistudio/claude-code-router)
- [Issue #964: Unable to configure Minimax M2 in router](https://github.com/musistudio/claude-code-router/issues/964)
- [Issue #1317: Anthropic-native providers double-converted](https://github.com/musistudio/claude-code-router/issues/1317)
- [MiniMax 开放平台文档](https://platform.minimaxi.com/docs/anthropic)
- [claude-code-mux](https://github.com/9j/claude-code-mux) (Rust 替代方案)