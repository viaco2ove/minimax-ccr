---
title: CCRG 配置 Schema 设计
version: 0.1.0-draft
date: 2026-05-11
---

# .gateway.json 配置 Schema

## 1. 设计原则

1. **语义清晰** — 字段名直接表达意图，不用 transformer/use 这种间接表达
2. **能力声明** — Provider 必须声明自己能做什么，Gateway 据此做路由决策
3. **环境变量** — 敏感信息通过 `$ENV_VAR` 引用，不明文存储
4. **每条规则自带 fallback** — 不依赖全局 fallback，粒度更细
5. **可扩展** — 路由策略可组合、可新增，不需要改代码

## 2. 完整 Schema

```jsonc
{
  // ═══════════════════════════════════════════════════════
  // 服务配置 — Gateway 自身的运行参数
  // ═══════════════════════════════════════════════════════
  "server": {
    "host": "127.0.0.1",           // 监听地址
    "port": 3458,                   // 监听端口（避免与 CCR 3456 冲突）
    "timeout_ms": 600000,           // 请求超时（10 分钟）
    "log_level": "info",            // debug | info | warn | error
    "log_file": "logs/ccrg.log",    // 日志文件路径
    "proxy_url": "",                // HTTP 代理（可选）
    "api_key": "",                  // Gateway 自身的认证 key（可选，留空则不校验）
    "claude_path": ""               // Claude Code 可执行文件路径（可选）
  },

  // ═══════════════════════════════════════════════════════
  // Provider 定义 — 上游 LLM 服务
  // ═══════════════════════════════════════════════════════
  "providers": {
    "<provider_name>": {
      // ── 连接配置 ──
      "api_base_url": "string",     // Provider 的 API 地址
      "api_key": "string",          // API Key，支持 $ENV_VAR 引用
      "protocol": "anthropic|openai",  // 通信协议

      // ── 模型列表 ──
      "models": ["string"],         // 该 Provider 支持的模型名

      // ── 能力声明 ──
      // Gateway 根据这些能力做路由决策
      "capabilities": {
        "tool_use": true,           // 是否支持 function calling / tool_use
        "streaming": true,          // 是否支持 SSE streaming
        "thinking": false,          // 是否支持 extended thinking
        "vision": true,            // 是否支持图片输入
        "max_context": 128000       // 最大上下文 token 数
      },

      // ── 成本分级 ──
      // 用于路由决策和成本优化
      "cost_tier": "cheap|standard|premium",

      // ── Provider 级别的请求参数覆盖 ──
      // 发往该 Provider 的请求会合并这些参数
      "default_params": {
        "temperature": 0.0,
        "max_tokens": 4096
      },

      // ── 重试策略 ──
      "retry": {
        "max_attempts": 2,          // 最大重试次数（不含首次请求）
        "retry_on_status": [429, 500, 502, 503]  // 触发重试的 HTTP 状态码
      }
    }
  },

  // ═══════════════════════════════════════════════════════
  // 路由规则 — 请求如何分发到 Provider
  // ═══════════════════════════════════════════════════════
  "routing": {
    // ── 默认路由 ──
    // 所有规则都未命中时的兜底
    "default": "provider_name:model_name",

    // ── 场景路由 ──
    // 基于请求的结构化特征（有无 thinking / web_search tools 等）
    "scenarios": {
      "<scenario_name>": {
        "route": "provider_name:model_name",
        "fallback": ["provider_name:model_name", ...],  // fallback 链，按顺序尝试
        "threshold": 60000   // 仅 long_context 场景需要
      }
    },

    // ── Tool 类型路由（核心创新） ──
    // 根据请求中触发的 tool 类型决定路由
    "tool_routing": {
      "<rule_name>": {
        "match": ["ToolName", "ToolName(subcommand)", ...],  // 支持精确匹配和模式匹配
        "match_mode": "any|all",  // any=命中任一即可，all=所有 tool 都必须匹配
        "route": "provider_name:model_name",
        "fallback": ["provider_name:model_name", ...]
      }
    },

    // ── 关键词路由 ──
    // 根据 prompt 中的关键词决定路由（最简单的分类）
    "keyword_routing": {
      "rules": [
        {
          "keywords": ["string"],
          "match_mode": "any|all",   // any=命中任一关键词，all=所有关键词都出现
          "scope": "user_message|all_messages|system_prompt",  // 搜索范围
          "route": "provider_name:model_name",
          "fallback": ["provider_name:model_name", ...]
        }
      ]
    },

    // ── 路由优先级 ──
    // 决定各路由策略的匹配顺序
    "priority": ["scenario", "tool_routing", "keyword_routing", "default"]
  },

  // ═══════════════════════════════════════════════════════
  // 套餐/额度管理 — CodePlan 等套餐的用量追踪
  // ═══════════════════════════════════════════════════════
  "quota": {
    "<quota_name>": {
      "enabled": true,
      "provider": "provider_name",
      "track_usage": true,           // 是否追踪用量
      "fallback_on_exhaust": "provider_name:model_name",  // 额度耗尽时走这里
      "warning_threshold": 0.2       // 剩余额度低于 20% 时告警
    }
  }
}
```

## 3. 字段详细说明

### 3.1 `providers.<name>.protocol`

决定 Gateway 如何与该 Provider 通信：

| 值 | 含义 | 请求处理 | 响应处理 |
|----|------|----------|----------|
| `anthropic` | Provider 使用 Anthropic Messages API 格式 | 直接透传，或做轻微参数调整 | 直接透传 SSE |
| `openai` | Provider 使用 OpenAI Chat Completions 格式 | anthropic → openai 格式转换 | openai → anthropic 格式转换 |

**注意**: Claude Code 始终以 Anthropic 格式发请求，Gateway 接收后根据目标 Provider 的 protocol 决定是否转换。

### 3.2 `providers.<name>.capabilities`

Gateway 的路由引擎会参考这些能力声明：

- `tool_use=false` 的 Provider 不会被分配需要 tool_use 的请求
- `vision=false` 的 Provider 不会被分配包含图片的请求
- `thinking=false` 的 Provider 不会被分配 scenario=think 的请求
- `max_context` 用于判断是否触发 long_context 场景

### 3.3 `providers.<name>.cost_tier`

| 值 | 含义 | 典型场景 |
|----|------|----------|
| `cheap` | 低成本 | 读取文件、搜索、简单解释 |
| `standard` | 中等成本 | 文件编辑、代码生成 |
| `premium` | 高成本 | 复杂推理、架构设计、调试 |

cost_tier 不直接决定路由，但可以用于：
- 路由规则的辅助判断
- 成本统计和报告
- quota 额度计算

### 3.4 `providers.<name>.default_params`

发往该 Provider 的请求会合并这些参数。用于处理不同 Provider 的参数差异：

```jsonc
"minimax": {
  "default_params": {
    "max_tokens": 4096   // MiniMax 需要显式设置 max_tokens
  }
}
```

### 3.5 `providers.<name>.api_key`

支持环境变量引用，格式为 `$ENV_VAR` 或 `${ENV_VAR}`：

```jsonc
"deepseek": {
  "api_key": "$DEEPSEEK_API_KEY"     // 从环境变量读取
}
```

如果环境变量不存在，启动时会报错。

### 3.6 `routing.scenarios`

预定义的场景类型及其判断条件：

| 场景名 | 触发条件 |
|--------|----------|
| `think` | 请求包含 `thinking` 字段 |
| `background` | 模型名包含 haiku（或用户自定义的"轻量模型"标识） |
| `long_context` | token 数 > threshold |
| `web_search` | tools 中包含 `web_search_*` 类型的工具 |
| `image` | 请求中包含图片内容 |

用户可以自定义场景名，但上述 5 个有内置的判断逻辑。自定义场景需要通过 `keyword_routing` 或 `tool_routing` 来匹配。

### 3.7 `routing.tool_routing.<rule>.match`

支持两种匹配模式：

**精确匹配**: `"Read"` — 请求中包含 Read tool 的调用结果时匹配

**模式匹配**: `"Bash(git status)"` — 用 `ToolName(subcommand)` 的格式，匹配 Bash tool 中以 git status 开头的命令

匹配规则：
- tool 名称部分不区分大小写
- subcommand 部分区分大小写，支持前缀匹配
- `*` 通配符：`"Bash(git *)"` 匹配所有 git 子命令

### 3.8 `routing.tool_routing.<rule>.match_mode`

| 值 | 含义 |
|----|------|
| `any` | 请求中命中任一 match 项即触发该规则 |
| `all` | 请求中所有 tool 调用都必须匹配才触发 |

通常用 `any`。`all` 的场景举例：确保一个只含 Read/Glob 的请求走 cheap provider，但如果同时有 Edit，则不走。

### 3.9 `routing.keyword_routing.rules[].scope`

| 值 | 搜索范围 |
|----|----------|
| `user_message` | 只搜索用户最新的一条消息 |
| `all_messages` | 搜索所有消息 |
| `system_prompt` | 只搜索系统提示词 |

默认 `user_message`，避免全量搜索的开销。

## 4. 完整示例

```jsonc
{
  "server": {
    "host": "127.0.0.1",
    "port": 3458,
    "timeout_ms": 600000,
    "log_level": "info",
    "log_file": "logs/ccrg.log",
    "proxy_url": "",
    "api_key": "",
    "claude_path": ""
  },

  "providers": {
    "minimax": {
      "api_base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "$MINIMAX_API_KEY",
      "protocol": "anthropic",
      "models": ["MiniMax-M2.7"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": false,
        "vision": true,
        "max_context": 128000
      },
      "cost_tier": "cheap",
      "default_params": {
        "max_tokens": 4096
      },
      "retry": {
        "max_attempts": 2,
        "retry_on_status": [429, 500, 502, 503]
      }
    },
    "qianfan": {
      "api_base_url": "https://qianfan.baidubce.com/anthropic/coding",
      "api_key": "$QIANFAN_API_KEY",
      "protocol": "anthropic",
      "models": ["qianfan-code-latest"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": false,
        "vision": true,
        "max_context": 128000
      },
      "cost_tier": "cheap",
      "retry": {
        "max_attempts": 1,
        "retry_on_status": [429, 500]
      }
    },
    "deepseek": {
      "api_base_url": "https://api.deepseek.com",
      "api_key": "$DEEPSEEK_API_KEY",
      "protocol": "openai",
      "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": true,
        "vision": true,
        "max_context": 64000
      },
      "cost_tier": "premium",
      "retry": {
        "max_attempts": 2,
        "retry_on_status": [429, 500, 502, 503]
      }
    }
  },

  "routing": {
    "default": "minimax:MiniMax-M2.7",

    "scenarios": {
      "background": {
        "route": "deepseek:deepseek-chat",
        "fallback": ["minimax:MiniMax-M2.7"]
      },
      "think": {
        "route": "deepseek:deepseek-reasoner",
        "fallback": ["deepseek:deepseek-chat", "minimax:MiniMax-M2.7"]
      },
      "long_context": {
        "route": "minimax:MiniMax-M2.7",
        "fallback": ["deepseek:deepseek-chat"],
        "threshold": 60000
      },
      "web_search": {
        "route": "deepseek:deepseek-v4-pro",
        "fallback": ["deepseek:deepseek-chat"]
      },
      "image": {
        "route": "minimax:MiniMax-M2.7",
        "fallback": ["deepseek:deepseek-v4-pro"]
      }
    },

    "tool_routing": {
      "cheap_tasks": {
        "match": [
          "Read", "Glob", "Grep",
          "Bash(git status)", "Bash(git diff)", "Bash(git log)",
          "Bash(ls *)", "Bash(cat *)", "Bash(head *)","ToolSearch"
        ],
        "match_mode": "all",
        "route": "minimax:MiniMax-M2.7",
        "fallback": ["qianfan:qianfan-code-latest"]
      },
      "standard_tasks": {
        "match": ["Edit", "Write", "NotebookEdit"],
        "match_mode": "any",
        "route": "minimax:MiniMax-M2.7",
        "fallback": ["deepseek:deepseek-chat"]
      },
      "complex_tasks": {
        "match": ["Agent", "TaskCreate", "EnterPlanMode"],
        "match_mode": "any",
        "route": "deepseek:deepseek-v4-pro",
        "fallback": ["deepseek:deepseek-chat", "minimax:MiniMax-M2.7"]
      }
    },

    "keyword_routing": {
      "rules": [
        {
          "keywords": ["搜索", "search", "联网", "web search"],
          "match_mode": "any",
          "scope": "user_message",
          "route": "deepseek:deepseek-v4-pro",
          "fallback": ["deepseek:deepseek-chat"]
        },
        {
          "keywords": ["图片", "截图", "image", "screenshot"],
          "match_mode": "any",
          "scope": "user_message",
          "route": "minimax:MiniMax-M2.7",
          "fallback": ["deepseek:deepseek-v4-pro"]
        },
        {
          "keywords": ["架构", "architecture", "重构", "refactor"],
          "match_mode": "any",
          "scope": "user_message",
          "route": "deepseek:deepseek-v4-pro",
          "fallback": ["deepseek:deepseek-chat"]
        }
      ]
    },

    "priority": ["scenario", "tool_routing", "keyword_routing", "default"]
  },

  "quota": {
    "minimax_codeplan": {
      "enabled": true,
      "provider": "minimax",
      "track_usage": true,
      "fallback_on_exhaust": "deepseek:deepseek-chat",
      "warning_threshold": 0.2
    }
  }
}
```

## 5. 与旧配置的映射

| 旧字段 (.gateway.json / CCR) | 新字段 | 说明 |
|------|------|------|
| `Providers[].name` | `providers.<name>` | 从数组改为字典，name 作为 key |
| `Providers[].api_base_url` | `providers.<name>.api_base_url` | 不变 |
| `Providers[].api_key` | `providers.<name>.api_key` | 支持 $ENV_VAR |
| `Providers[].transformer.use` | `providers.<name>.protocol` | 语义更清晰 |
| `Providers[].models` | `providers.<name>.models` | 不变 |
| `Router.default` | `routing.default` | 格式从 `"provider,model"` 改为 `"provider:model"` |
| `Router.think` | `routing.scenarios.think.route` | 嵌套结构 |
| `Router.longContextThreshold` | `routing.scenarios.long_context.threshold` | 归属到场景内部 |
| `fallback.default[]` | `routing.scenarios.default.fallback` | 每条规则自带 |
| `LOG` / `LOG_LEVEL` | `server.log_level` | 归到 server |
| `HOST` / `PORT` | `server.host` / `server.port` | 归到 server |
| `APIKEY` | `server.api_key` | 归到 server |
| `API_TIMEOUT_MS` | `server.timeout_ms` | 归到 server |
| `StatusLine` | _(移除)_ | CCRG 不处理 StatusLine |
| `CUSTOM_ROUTER_PATH` | _(移除)_ | 通过扩展 classifier 替代 |

## 6. 配置校验规则

启动时校验，校验失败则拒绝启动：

1. `routing.default` 必须存在
2. 所有 `route` 和 `fallback` 中的 `provider:model` 必须在 `providers` 中有定义
3. `scenario` 类型为 `think` 的规则，其 route 的 provider 必须有 `capabilities.thinking=true`
4. `scenario` 类型为 `image` 的规则，其 route 的 provider 必须有 `capabilities.vision=true`
5. `routing.priority` 中的每个值必须是合法的策略名
6. `api_key` 中的 `$ENV_VAR` 引用的环境变量必须存在
7. `providers` 中至少有一个 provider
