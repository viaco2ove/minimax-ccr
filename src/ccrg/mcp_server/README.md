# CCRG MCP Server

提供 CCRG 能力给 qoder IDE 等 MCP 客户端调用。

## 架构

MCP 路由直接挂载到 CCRG 主服务（端口 3428），共用同一个端口，无需单独启动。

```
qoder IDE ──SSE/JSON-RPC──▶ CCRG:3428/mcp/sse
                            │
                            ├── /v1/messages      (Claude Code CLI)
                            ├── /v1/chat/completions (OpenAI 格式)
                            ├── /mcp              (MCP JSON-RPC)
                            ├── /mcp/sse          (MCP SSE)
                            └── /dashboard        (Dashboard)
```

## 提供的工具

| 工具 | 说明 |
|---|---|
| `ccrg_chat` | 发送 Chat 请求（支持 openai / anthropic 两种格式）|
| `ccrg_route` | 预览路由决策（不发送请求）|
| `ccrg_stats` | 获取使用统计 |
| `ccrg_health` | 检查 CCRG 健康状态 |

## qoder IDE 配置

```json
{
  "mcpServers": {
    "ccrg": {
      "url": "http://127.0.0.1:3428/mcp/sse"
    }
  }
}
```

## 独立运行（调试用）

```bash
python -m src.ccrg.mcp_server --ccrg-url http://127.0.0.1:3428 --port 3500
```

## API 测试

```bash
# 列出工具
curl -X POST http://127.0.0.1:3428/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 检查健康
curl -X POST http://127.0.0.1:3428/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ccrg_health","arguments":{}}}'

# 发送聊天
curl -X POST http://127.0.0.1:3428/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ccrg_chat","arguments":{"messages":[{"role":"user","content":"hello"}]}}}'
```