# CCRG MCP Server

提供 CCRG 能力给 qoder IDE 等 MCP 客户端调用。

## 架构

MCP 路由直接挂载到 CCRG 主服务（共用端口），无需单独启动。

## 工具列表

| 工具 | 说明 |
|---|---|
| `ccrg_chat` | 发送 Chat 请求（支持 openai / anthropic 两种格式）|
| `ccrg_code` | **复杂编程工具** — 读文件、写代码、执行命令、规划任务、审查代码 |
| `ccrg_route` | 预览路由决策（不发送请求）|
| `ccrg_stats` | 获取使用统计 |
| `ccrg_health` | 检查 CCRG 健康状态 |

## ccrg_code — 复杂编程能力

qoder IDE 可以通过 `ccrg_code` 工具实现多步骤编程：

### task_type

| 类型 | 说明 | 输入参数 |
|---|---|---|
| `plan` | 规划任务 | task + files/context |
| `read` | 读取并分析文件 | task + files/context |
| `write` | 生成/修改代码 | task + files/file_contents（自动写入）|
| `exec` | 执行命令并分析 | task + commands/context |
| `review` | 审查代码 | task + files/file_contents |
| `chat` | 普通编程对话 | task + context |

### 多步骤编程示例

qoder IDE 可以这样编排多步编程：

```
第1步：规划
  ccrg_code(task_type="plan", task="重构 J03_chat_group_job.kjb，拆分为独立的子任务", files=["script/kettle/.../J03_chat_group_job.kjb"])

第2步：读取现有代码
  ccrg_code(task_type="read", task="分析这个 kjb 的结构", files=["script/kettle/.../J03_chat_group_job.kjb"])

第3步：执行命令验证
  ccrg_code(task_type="exec", task="运行 pentaho 转换看看报错", commands=["kitchen.sh -file=script/.../test.kjb"])

第4步：写入新代码
  ccrg_code(task_type="write", task="创建拆分后的子任务文件", files=["script/kettle/.../J03_chat_group_job.kjb"])

第5步：审查
  ccrg_code(task_type="review", task="审查新生成的代码", files=["script/data_check/.../check.py"])
```

## 测试

```bash
# 列出工具
curl -X POST http://127.0.0.1:3429/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 测试 ccrg_code plan
curl -X POST http://127.0.0.1:3429/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ccrg_code","arguments":{"task_type":"plan","task":"为一个 FastAPI 项目添加用户认证模块","files":["src/main.py"]}}}'

# 测试 ccrg_code exec
curl -X POST http://127.0.0.1:3429/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ccrg_code","arguments":{"task_type":"exec","task":"运行测试看看有没有报错","commands":["python -m pytest tests/ -v"]}}}'
```

## qoder IDE 配置

```json
{
  "mcpServers": {
    "ccrg": {
      "url": "http://127.0.0.1:3429/mcp/sse"
    }
  }
}
```