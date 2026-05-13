# Claude Code 连接 CCRG 配置

## 配置文件位置

| 系统 | 路径 |
|------|------|
| Windows | `C:\Users\<用户名>\.claude\settings.json` |
| macOS/Linux | `~/.claude/settings.json` |

## 配置内容

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3458",
    "ANTHROPIC_AUTH_TOKEN": "local",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  }
}
```

## 配置说明

| 配置项 | 说明 |
|--------|------|
| `ANTHROPIC_BASE_URL` | CCRG 监听地址，固定 `http://127.0.0.1:3458` |
| `ANTHROPIC_AUTH_TOKEN` | 认证 token，填 `local` 即可（CCRG 会忽略） |
| `API_TIMEOUT_MS` | 请求超时，600000ms = 10 分钟 |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | 关闭非必要流量，减少干扰 |

## 启动顺序

```bash
# 1. 先启动服务
run.ccr.bat

# 2. 再启动 Claude Code
claude
```

## 验证

服务启动后，访问 http://127.0.0.1:3458/health 确认 CCRG 运行正常：

```json
{"status":"ok","providers":["minimax","qianfan","deepseek","mmx"]}
```

## 注意事项

1. **端口 3457** 是 mmx_provider（本地 MiniMax）
2. **端口 3458** 是 CCRG（智能路由网关）
3. Claude Code 只需要指向 CCRG（3458），不需要直接连接 mmx_provider
4. `.gateway.json` 中的 API keys 是各个 provider 的密钥，与 Claude Code 配置无关
