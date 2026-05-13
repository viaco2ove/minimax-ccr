# Minimax
```
"minimax": {
      "api_base_url": "https://api.minimaxi.com/anthropic",
      "api_key": "sk-cp-BgYFK1oZIWVhfvsS5N70jzERxXTlYSorFFZg5oobA8B46udD6zS0kzQ7cjAjZgjQDPruhXMI8inZpn2YoYDdWr0JgM2CJtz_x78DT80FIuetWFZQUXLuThw",
      "protocol": "codeplan_anthropic",
      "models": ["MiniMax-M2.7"],
      "capabilities": {
        "tool_use": true,
        "streaming": true,
        "thinking": false,
        "vision": true,
        "max_context": 128000
      },
      "cost_tier": "cheap",
      "timeout_ms": 1600000,
      "default_params": {
        "max_tokens": 4096
      },
      "retry": {
        "max_attempts": 4,
        "retry_on_status": [429, 500, 502, 503]
      }
    },
    "mmx": {
      "api_base_url": "https://127.0.0.1:3457",
      "api_key": "local",
      "protocol": "mmx",
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
    }
```

# ccswitch 是如何对接minimax 