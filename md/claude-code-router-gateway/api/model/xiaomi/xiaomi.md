## codeplan
"providers_adapter": "xiaomi",

```
"xiaomi": {
      "api_base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
      "api_key": "sk-xxx",
      "protocol": "codeplan_anthropic",
      "models": ["mimo-v2.5-pro"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": true,
        "vision": true,
        "max_context": 32000
      },
      "per_request_delay_ms": 0,
      "cost_tier": "cheap",
      "timeout_ms": 120000,
      "retry": {
        "max_attempts": 3,
        "retry_on_status": [429, 500, 502, 503]
      }
    },
```

## 非codeplan , 因为不命中codeplan 所以会额外扣钱的
```
"xiaomi": {
      "api_base_url": "https://api.xiaomimimo.com/anthropic",
      "api_key": "sk-xxx",
      "protocol": "codeplan_anthropic",
      "models": ["mimo-v2.5-pro"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": true,
        "vision": true,
        "max_context": 32000
      },
      "per_request_delay_ms": 0,
      "cost_tier": "cheap",
      "timeout_ms": 120000,
      "retry": {
        "max_attempts": 3,
        "retry_on_status": [429, 500, 502, 503]
      }
    },
```

官方准确命名与定位，我之前写反了一个关键细节：
✅ mimo-v2.5 = 官方全称mimo-v2.5-omni = 全模态通用模型（什么都能干）
✅ mimo-v2.5-pro = 纯文本 Agent / 代码专用旗舰模型（只干代码和长任务，别的一概不管）