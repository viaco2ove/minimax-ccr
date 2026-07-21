# CLAUDE.md — Claude Code Router Gateway (CCRG)

## 项目概述

CCRG 是一个基于 FastAPI 的 AI 请求智能路由网关，支持在同一 agent loop 内根据请求特征（场景、tool 类型、关键词）动态路由到不同 Provider。

**核心解决的问题**：
- 便宜模型（MiniMax-M2.7）处理简单任务降低成本
- 复杂推理任务路由到支持 thinking 的模型
- 图像理解任务路由到支持 vision 的模型
- 统一接口，客户端无需关心后端 Provider 差异

## 技术栈

- Python 3.11+ / FastAPI / httpx / uvicorn
- 协议支持：Anthropic Messages API、OpenAI Chat API、MiniMax 专用协议

## 目录结构

```
src/ccrg/
├── main.py              # FastAPI 入口，/v1/messages 端点
├── config.py             # 配置加载、校验、环境变量解析
├── types.py              # 数据类型（RequestTags, RouteResult, ProviderConfig）
├── usage_stats.py        # Token 使用统计
├── classifier/           # 请求分类器（并行执行）
│   ├── scenario.py       # 场景检测（think/image/web_search/background/compact）
│   ├── tool_type.py      # Tool 调用提取（Bash/Read/Write/Grep 等）
│   └── keyword.py       # 关键词匹配
├── router/
│   └── engine.py         # 路由引擎（4 阶段优先级匹配）
├── provider/
│   └── registry.py       # Provider 注册表（名称→配置映射）
├── protocol/             # 协议适配器
│   ├── base.py           # ProtocolAdapter 基类
│   ├── anthropic_adapter.py  # Anthropic 协议转换（核心适配逻辑）
│   ├── minimax_adapter.py    # MiniMax 专用适配器（继承自 Anthropic）
│   └── openai_adapter.py     # OpenAI 协议适配器
├── protocol/anthropic_sse.py # Anthropic SSE 流式解析
└── protocol/openai_sse.py   # OpenAI SSE 流式解析
```

## 配置说明

配置文件：`.gateway.json`（项目根目录）

### Provider 配置

```json
{
  "providers": {
    "minimax": {
      "api_base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "${MINIMAX_API_KEY}",
      "protocol": "codeplan_anthropic",
      "models": ["MiniMax-M2.7"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": true,
        "vision": true,
        "max_context": 32000
      },
      "cost_tier": "cheap"
    }
  },
  "routing": {
    "priority": ["scenario", "tool_routing", "keyword_routing", "default"],
    "default": "minimax:MiniMax-M2.7",
    "scenarios": {
      "think": {
        "route": "deepseek:deepseek-reasoner",
        "fallback": ["minimax:MiniMax-M2.7"]
      }
    },
    "tool_routing": {
      "cheap_tasks": {
        "match": ["Read", "Glob", "Grep"],
        "match_mode": "any",
        "route": "minimax:MiniMax-M2.7",
        "fallback": ["qianfan:qianfan-code-latest"]
      }
    }
  }
}
```

### protocol 说明（重要）

- `codeplan_anthropic`：走 Codeplan（agent 工具）版 Anthropic 接口，api_key 与对话接口**不通**
- `chat_openai`：走"对话 API"，不是 Codeplan 接口
- `mmx`：走独立的 mmx 命令方式，支持图形理解

### max_context 效果

控制消息截断阈值。当预估 token 数 > max_context × 80% 时，自动截断消息保留最近对话。

- minimax 配置 max_context: 32000 → 截断阈值 = 25600 tokens
- qianfan/doubao 配置 max_context: 128000 → 截断阈值 = 102400 tokens
已改为不截断！
[400.md](md/error/400.md)
## 路由机制

### 优先级顺序

1. **scenario** — 按请求场景路由（think/image/web_search 等）
2. **tool_routing** — 按 tool 调用类型路由（核心创新）
3. **keyword_routing** — 按关键词匹配路由
4. **default** — 兜底默认路由

### Tool 路由模式

```json
"tool_routing": {
  "cheap_tasks": {
    "match": ["Read", "Glob", "Grep"],
    "match_mode": "any",
    "route": "minimax:MiniMax-M2.7"
  }
}
```

匹配格式：`ToolName(subcommand)` 或 `ToolName(subcommand*)`（前缀匹配）

### 能力降级

当目标 Provider 不支持请求所需能力（thinking/vision/tool_use）时，自动尝试 fallback chain 中的 Provider。

如果 fallback 都不满足能力需求，Adapter 会剥离不支持的内容（如移除 thinking 块）。

## AnthropicAdapter 核心处理逻辑

`src/ccrg/protocol/anthropic_adapter.py` 的 `transform_request()` 方法包含 8 大类兼容性处理：

1. 合并 default_params
2. 确保 max_tokens 存在（默认 4096）
3. 清理 system-reminder（正则移除 ` reminds` 块）
4. 剥离 thinking（如果 provider 不支持）
5. 剥离/替换 image 内容块（如果 provider 不支持 vision）
6. 处理 output_config.effort（映射 xhigh→high，无效值→medium）
7. codeplan_anthropic 协议特殊处理：
   - 剥离 output_config（非原生参数）
   - 清理 thinking.budget_tokens
   - 映射 tool_choice 的 any/tool → auto
   - 将 system list 转为纯字符串
   - 清理 tools 中的 cache_control
   - 限制 max_tokens 上限 32K
   - 截断过长 system prompt（>8000 字符）
## 运行

```bash
# 开发模式
python -m src.ccrg.main

# Windows
./run.ccr.bat

# 端口：127.0.0.1:3428
# Dashboard: http://127.0.0.1:3428/stats
```

## 调试

1. 日志文件：`logs/ccrg.log`
2. 配置 `log_level: "debug"` 获取详细路由决策日志
3. 检查 `/health` 端点确认 Provider 连接状态

## 注意事项

- **api_key 安全**：不要在样例文件中记录真实 api_key，发布前执行 `sed 's/"api_key": ".*"/"api_key": "xxxx"/'`
- **per_request_delay_ms**：控制请求速率，避免触发限流
- **环境变量**：支持 `$VAR` 和 `${VAR}` 格式，也支持 `${VAR}_FILE` 从文件读取


## 运行环境 3428
3428 用于正式使用。作为隔离手段。不然边修改边用会很麻烦
D:\Users\viaco\PycharmProjects\minimax-ccr-run
禁止直接修改minimax-ccr-run！修改的是 minimax-ccr！
用户自己会更新minimax-ccr-run

## 调试开发环境 3429
用于开发测试。源代码修改 。上传git.
改好的代码复制到运行环境 3428
由用户自己复制过去minimax-ccr-run，不需要ai自动复制过去
不允许ai修改minimax-ccr-run！！！