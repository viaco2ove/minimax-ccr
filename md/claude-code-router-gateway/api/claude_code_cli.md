{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3428",
    "ANTHROPIC_AUTH_TOKEN": "local",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "skipDangerousModePermissionPrompt": true
}

可以配置 上下文限制

{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "local",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3428",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    // 适配MiniMax M2公有云32768总窗口
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "30000",
    "DISABLE_COMPACT": "1",
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
