# 配置方案
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3428",
    "ANTHROPIC_AUTH_TOKEN": "local",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "skipDangerousModePermissionPrompt": true
}

# 可以配置 上下文限制
```
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "local",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3428",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    // 适配MiniMax M2公有云32768总窗口
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "128000",
    "DISABLE_COMPACT": "0",
    // 工具单次返回上限，避免一次性读超大文件
    "MAX_MCP_OUTPUT_TOKENS": "8000",
    // 限制推理思考token
    "MAX_THINKING_TOKENS": "3000",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
    // 80%占用自动压缩上下文
    "CLAUDE_AUTOCOMPACT_PCT": "80"
  },
  "permissions": {
    "defaultMode": "auto"
  },
  "skipAutoPermissionPrompt": true,
  "skipDangerousModePermissionPrompt": true
}
```
只要你写了 `MAX_THINKING_TOKENS` 限制思考长度，**必须搭配 `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1"`**，否则 `MAX_THINKING_TOKENS` 直接失效，等于白写配置。
LAUDE\_CODE\_DISABLE\_NONESSENTIAL\_TRAFFIC 完整说明
值设为 `"1"` 时，**一次性关闭 Claude Code 所有非核心外网请求**，等价同时开启 4 个独立关闭开关：
`DISABLE_AUTOUPDATER`、`DISABLE_FEEDBACK_COMMAND`、`DISABLE_ERROR_REPORTING`、`DISABLE_TELEMETRY`code.claud...

# 去注释版
```
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "local",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3428",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_AUTOCOMPACT_PCT": "80",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "128000",
    "DISABLE_COMPACT": "0",
    "MAX_MCP_OUTPUT_TOKENS": "8000",
    "MAX_THINKING_TOKENS": "3000"
  },
  "permissions": {
    "defaultMode": "auto"
  },
  "skipAutoPermissionPrompt": true,
  "skipDangerousModePermissionPrompt": true
}
```