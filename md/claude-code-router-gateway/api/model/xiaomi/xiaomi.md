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
