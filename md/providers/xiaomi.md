# Minimax
```
"xiaomi": {
      "api_base_url": "https://token-plan-cn.xiaomimimo.com/anthropic",
      "api_key": "xxx",
      "protocol": "codeplan_anthropic",
      "providers_adapter": "xiaomi",
      "models": ["mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-omni"],
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
    }
```

## max_context 的效果：控制消息截断阈值。

  当预估 token 数 > max_context * 80% 时，会自动截断消息保留最近的对话，使总 token 控制在安全范围内。

  - minimax 配置 max_context: 32000 → 截断阈值 = 25600 tokens
  - qianfan 配置 max_context: 128000 → 截断阈值 = 102400 tokens

  所以不同 provider 的 max_context 决定了它们能"吃下"多大的上下文，越大的模型截断越宽松。你的 minimax 配置比较保守（32k），qianfan 更大（128k）。

## providers_adapter
"providers_adapter": "xiaomi" 针对xiaomi 官网 接口 的/使用的适配器  
# ccswitch 是如何对接xiaomi

图片直接404？

 D:\Users\viaco\PycharmProjects\minimax-ccr-run\logs\req\req_f187115a_xiaomi.json 不行，源接口 返回的
{
    "error": {
        "code": "404",
        "message": "No endpoints found that support image input",
        "param": "",
        "type": ""
    }
}
所以是我们日志没有打印日志导致的，理解有误

但是为什么会报这个错？

首先看看这个操作是 /compact 引发的
然后怎么就跑到

{
  "keywords": ["/compact", "compact"],
  "match_mode": "any",
  "scope": "user_message",
  "route": "minimax:MiniMax-M2.7",
  "fallback": ["xiaomi:mimo-v2.5-pro","xiaomi:mimo-v2.5","minimax:MiniMax-M2.7"]
}

"compact": {
"route": "minimax:MiniMax-M2.7",
"fallback": [""xiaomi:mimo-v2.5","minimax:MiniMax-M2.7"]
}

直接 "xiaomi:mimo-v2.5-pro"改成"xiaomi:mimo-v2.5" 吧。然后修复这种情况的fallback 问题。
