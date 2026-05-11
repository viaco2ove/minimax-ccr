---
title: Claude Code Router Gateway - 架构概述
version: 0.1.0-draft
date: 2026-05-11
---

# Claude Code Router Gateway (CCRG)

## 1. 项目定位

CCRG 是一个**极薄的 AI 请求网关**，部署在 Claude Code 和上游 LLM Provider 之间。

核心目标：**在同一 agent loop 内，根据请求特征动态路由到不同 provider**，实现成本优化和能力匹配。

### 与 CCR 的关系

| 维度 | CCR | CCRG |
|------|-----|------|
| 本质 | 协议统一层 + 简单路由 | 智能路由网关 |
| 路由策略 | 6 个硬编码场景 | 多策略可组合 + 可扩展 |
| 动态切换 | 一次会话固定模型 | 同一会话内按请求动态切换 |
| 请求分类 | 无 | 按 tool 类型 / prompt 特征 / 关键词 |
| 协议转换 | 完整 transformer 管线 | 精简转换（仅 anthropic/openai） |
| fallback | 全局配置 | 每条路由规则自带 fallback 链 |
| 代码量 | ~3000 行 TS | 目标 <1000 行 Python |

**CCRG 不替代 CCR 的全部功能**，只聚焦"请求分类 + 智能路由"这一核心价值。如果只需要简单透传和协议统一，CCR 依然适用。

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│                      Claude Code                        │
│                   (thinks it's talking to Anthropic)     │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP POST /v1/messages
                        ▼
┌─────────────────────────────────────────────────────────┐
│                  CCRG (Gateway)                         │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │   Classifier │  │    Router     │  │   Protocol    │  │
│  │             │→│              │→│   Adapter     │  │
│  │ - scenario  │  │ - priority   │  │              │  │
│  │ - tool_type │  │ - fallback   │  │ - anthropic  │  │
│  │ - keyword   │  │ - quota      │  │ - openai     │  │
│  └─────────────┘  └──────────────┘  └──────┬───────┘  │
│                                             │          │
└─────────────────────────────────────────────┼──────────┘
                                              │
                    ┌─────────────────────────┼──────────────────┐
                    │                         │                  │
                    ▼                         ▼                  ▼
           ┌──────────────┐        ┌──────────────┐    ┌──────────────┐
           │   MiniMax    │        │   DeepSeek   │    │   Qianfan    │
           │  (CodePlan)  │        │   (Premium)  │    │   (Cheap)    │
           └──────────────┘        └──────────────┘    └──────────────┘
```

## 3. 请求处理流程

```
请求进入
    │
    ▼
[1] 解析请求 — 提取 model / messages / tools / system / stream
    │
    ▼
[2] 分类器 — 分析请求特征，输出 tags
    │   tags = { scenario, tool_types, keywords, token_count }
    │
    ▼
[3] 路由器 — 根据 tags + 路由规则，决定 provider + model
    │   route_result = { provider, model, fallback_chain }
    │
    ▼
[4] 协议适配 — 根据 provider.protocol 转换请求格式
    │   anthropic → anthropic: 透传
    │   anthropic → openai:    转换请求/响应
    │
    ▼
[5] 发送请求 — HTTP POST 到 provider.api_base_url
    │
    ├── 成功 → [6] 协议适配（反向）→ 返回给 Claude Code
    │
    └── 失败 → [7] fallback — 取 fallback_chain 中下一个 provider，回到 [4]
```

## 4. 核心概念

### 4.1 Provider

一个上游 LLM 服务，声明自身的能力和成本等级。

关键字段：
- `protocol`: 通信协议（anthropic / openai）
- `capabilities`: 能力声明（tool_use / streaming / thinking / vision / max_context）
- `cost_tier`: 成本分级（cheap / standard / premium）

Provider 是被动实体，Gateway 主动选择它。

### 4.2 Classifier

请求特征提取器，不直接做路由决策，只输出结构化的 tags。

```
input:  raw request
output: { scenario, tool_types[], keywords[], token_count, has_thinking, has_images }
```

分类器是可扩展的 — 后续可以加 LLM 分类器，但初期只用规则分类。

### 4.3 Router

根据 Classifier 输出的 tags 和配置的路由规则，决定请求发往哪个 Provider。

路由规则的匹配有优先级，先命中先执行。

### 4.4 Protocol Adapter

处理不同 provider 之间的协议差异。

目前只支持两种协议：
- **anthropic**: Claude Code 原生协议，直接透传即可
- **openai**: 需要 anthropic → openai 请求转换，以及 openai → anthropic 响应转换

### 4.5 Fallback Chain

每条路由规则自带 fallback 链。主 provider 失败时，按顺序尝试 fallback provider。

## 5. 技术选型

| 选择 | 方案 | 理由 |
|------|------|------|
| 语言 | Python 3.10+ | 与 mmx_provider.py 一致，生态成熟 |
| HTTP 框架 | FastAPI | async 原生支持、SSE streaming 友好、自带 OpenAPI 文档 |
| 配置格式 | JSON5（.gateway.json） | 兼容 CCR 配置习惯，支持注释 |
| 日志 | structlog | 结构化日志，方便后续分析路由决策 |
| Token 计数 | tiktoken (cl100k_base) | 与 CCR 一致，估算即可 |

## 6. 目录结构

```
minimax-ccr/
├── src/
│   └── ccrg/                        # Gateway 主代码
│       ├── __init__.py
│       ├── main.py                   # 入口，FastAPI app
│       ├── config.py                 # 配置加载 + 校验
│       ├── classifier/
│       │   ├── __init__.py
│       │   ├── base.py               # Classifier 基类
│       │   ├── scenario.py           # 场景分类（think/web_search/...）
│       │   ├── tool_type.py          # Tool 类型分类
│       │   └── keyword.py            # 关键词分类
│       ├── router/
│       │   ├── __init__.py
│       │   ├── engine.py             # 路由引擎
│       │   └── rules.py              # 路由规则定义
│       ├── protocol/
│       │   ├── __init__.py
│       │   ├── base.py               # Adapter 基类
│       │   ├── anthropic_adapter.py   # Anthropic 协议（透传）
│       │   └── openai_adapter.py      # OpenAI 协议（转换）
│       ├── provider/
│       │   ├── __init__.py
│       │   └── registry.py           # Provider 注册表
│       └── middleware/
│           ├── __init__.py
│           └── logging.py            # 请求/响应日志
├── .gateway.json                     # Gateway 配置
├── tests/
│   └── ccrg/                         # 测试
└── mmx_provider.py                   # 现有 MiniMax provider（保持独立）
```

## 7. 与 mmx_provider.py 的关系

mmx_provider.py 是一个独立的 MiniMax 本地 provider（通过 mmx CLI 调用）。

CCRG 不会替代 mmx_provider.py，而是通过配置将其作为一个 provider 接入：

```jsonc
"providers": {
  "minimax-local": {
    "api_base_url": "http://127.0.0.1:3457",
    "api_key": "local",
    "protocol": "anthropic",
    // ...
  }
}
```

两者独立运行，CCRG 只负责路由决策和协议转换，mmx_provider.py 负责 MiniMax 的具体调用。

## 8. 与 CCR 的兼容

CCRG 监听独立端口（默认 3458），不与 CCR（3456）冲突。

Claude Code 配置指向 CCRG 还是 CCR，取决于用户需求：
- 需要**智能路由** → 指向 CCRG
- 只需**简单透传** → 指向 CCR
- 需要 CCR 的其他功能 → CCRG 可以将 CCR 作为一个 provider 接入
