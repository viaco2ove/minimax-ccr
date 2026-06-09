---
name: ccrg-code
description: 通过 CCRG MCP 执行编程 Agent 任务。使用 SSE 流式接收，实时显示每一步执行过程，像 codex cli / claude code 一样工作。
---

# CCRG Code - 流式编程工作流

qoder 通过 SSE 流式接收 MCP 返回，实时显示每一步（exec → 分析 → write → exec ...）。
ccrg 只是对接了大模型的一个模型分流器，不是cli 。 
你：包含两种意义，一个是qoder 的正在对接的模型。 一个是 qoder + 模型 作为一个整体。
你（也就是qoder cli 或者 qoder ide 加模型 ） 调用CCRG MCP 获取要调用什么工具。读取什么文件。要做什么。然后你来实际操作.
你才是真正的工具调用方。

## ⚠️ 关键规则

1. **ccrg 不可用时，反馈给用户，不允许跳过 MCP 服务**
2. **ccrg 超时时，告诉用户超时，等待用户反馈，不自动下一步**
3. **使用 SSE 流式接收**：qoder 连接到 `/mcp/sse`，通过 `/mcp/messages` 发请求，实时收到每一步的推送
4. **max_rounds 控制循环轮数**：达到上限则停止

## 架构

```
qoder IDE (SSE 订阅)
  │
  ├─ GET /mcp/sse ──────────────► 建立 SSE 连接
  │◄───────────────────────────── 返回 endpoint
  │
  ├─ POST /mcp/messages ────────► tools/call(loop)
  │◄───────────────────────────── SSE 推送: Step 1: exec...
  │◄───────────────────────────── SSE 推送: Step 2: 读取文件...
  │◄───────────────────────────── SSE 推送: Step 3: LLM 分析...
  │◄───────────────────────────── SSE 推送: Step 4: 写入文件...
  │◄───────────────────────────── SSE 推送: done=true → 完成
```

## SSE 流式返回格式

每条 SSE 事件：
```
event: message
data: {"type": "step", "text": "## 🔄 Round 1/5\n**Step 1: 执行命令**\n...", "done": false}
```

最后一条：
```
event: message
data: {"type": "step", "text": "任务完成！", "done": true}
```

## 工具调用

```
Server: ccrg
Tool:   ccrg_code
```

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `task_type` | 是 | read/write/exec/loop/plan/review |
| `task` | 是 | 任务描述 |
| `files` | 否 | 文件路径列表 |
| `file_contents` | 否 | 文件内容 `[{path, content}]` |
| `commands` | 否 | 命令列表 |
| `context` | 否 | 额外上下文 |
| `model` | 否 | 指定模型 |
| `max_rounds` | 否 | loop 最大轮数（默认 500） |

## task_type

| task_type | 做什么 | 返回 |
|-----------|--------|------|
| `read` | 读取文件 → LLM 分析 | 分析报告 |
| `write` | LLM 生成代码 → 写入磁盘 | 写入状态 |
| `exec` | 执行命令 → LLM 分析 | 执行结果 |
| `loop` | **流式循环**：每步 SSE 推送，exec→分析→write→exec 直到成功 | `success:true/false` |
| `plan` | LLM 规划 | 实现方案 |
| `review` | LLM 审查 | 审查报告 |

## ⭐ 核心：loop 流式循环

**每一步都通过 SSE 实时推送**，qoder 实时看到进度：

```
## 🔄 Round 1/5

**Step 1: 执行命令**

`python validate.py` → ❌ 失败 (退出码: 1)
```
Traceback (most recent call last):
  File "validate.py", line 10, in <module>
    raise ValueError("数据不完整")
ValueError: 数据不完整
```

**Step 2: 读取相关文件**

- `script/validate.py` (1024 chars)

**Step 3: 分析错误 & 生成修复代码**

**LLM 分析:**
文件缺少空值检查，已生成修复代码...

**Step 4: 写入修复文件**

- ✅ 已写入: `script/validate.py` (1120 chars)
```

qoder 循环直到收到 `done: true`。

## SSE 调用示例

### 1. 建立 SSE 连接
```bash
curl -N http://127.0.0.1:3429/mcp/sse
# 返回: event: endpoint\ndata: http://127.0.0.1:3429/mcp/messages?sessionId=xxx\n\n
```

### 2. 发送 tools/call
```bash
curl -X POST http://127.0.0.1:3429/mcp/messages?sessionId=xxx \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "ccrg_code",
      "arguments": {
        "task_type": "loop",
        "task": "运行并修复 validate.py",
        "files": ["script/validate.py"],
        "commands": ["python script/validate.py"],
        "max_rounds": 5
      }
    }
  }'
# 返回: {"status": "accepted"}
# 然后 SSE 实时推送每一步
```

## ccrg_code_watcher.py

`ccrg_code_watcher.py` 帮你处理 SSE 流式接收：

```bash
python ccrg_code_watcher.py --task "运行并修复 validate.py" \
    --files script/validate.py \
    --commands "python script/validate.py" \
    --max-rounds 5
```

## qoder 集成

qoder 应该：
1. 建立 SSE 连接 `/mcp/sse`
2. 收到 endpoint 后，通过 `/mcp/messages` 发送 `tools/call`
3. SSE 实时收到每一步，display 给用户看
4. 收到 `done: true` 或达到 max_rounds 时停止

```javascript
// 伪代码示例
const sse = new EventSource('/mcp/sse');
sse.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'step') {
        show(data.text);  // 实时显示给用户
        if (data.done) {
            sse.close();
        }
    }
};

// 发送请求
fetch('/mcp/messages?sessionId=xxx', {
    method: 'POST',
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: {...} })
});
```

## 注意事项

1. **SSE 流式接收**：每一步都实时推送，用户看到逐步执行过程
2. **max_rounds 控制轮数**：达到上限停止循环
3. **成功则 done=true**：qoder 收到 done=true 时关闭 SSE
4. **超时处理**：如果超过 120 秒没有响应，提示用户超时