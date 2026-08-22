# Workflow Stage Tag 调研报告

## 问题背景

CCRG 需要在 workflow 流程中识别当前阶段（intention_analyze → execute_solve → analyze_plan → execute_write），以决定路由到哪个 provider。最初尝试通过 `body.metadata.workflow_stage` 由调用方（Claude Code）设置，但发现 **Claude Code 不会发送自定义 metadata 字段**。

## 调研方法

1. 分析 `D:\Users\viaco\PycharmProjects\minimax-ccr\logs\req\` 中的请求日志
2. 分析 `D:\Users\viaco\PycharmProjects\minimax-ccr-run\logs\req\` 中的运行日志
3. 检查 Claude Code 发送的请求结构
4. 检查 WORKFLOW_HINT 注入是否被 Claude Code 保留

---

## 调研发现

### 1. Claude Code 请求结构

Claude Code 发送给 CCRG 的请求包含以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | string | 模型名称 |
| `messages` | array | 完整对话历史 |
| `system` | string | 系统提示词（8000+ 字符） |
| `tools` | array | 可用工具列表 |
| `metadata` | object | **仅包含 `user_id`**（设备信息） |
| `max_tokens` | int | 最大输出 token |
| `context_management` | object | 仅包含 `edits`（thinking 清理） |
| `stream` | bool | 是否流式 |

**关键发现**：`metadata` 字段是固定的，只包含：
```json
{
  "user_id": "{\"device_id\":\"...\",\"account_uuid\":\"\",\"session_id\":\"...\"}"
}
```
**无法添加自定义字段**（如 `workflow_stage`）。

### 2. WORKFLOW_HINT 注入效果

CCRG 当前通过 HTML 注释注入 hint：
```html
<!-- WORKFLOW_HINT: 请在下一个请求的 metadata 中设置 "workflow_stage": "execute_solve" -->
```

#### 发现一：HTML 注释出现在对话摘要中

在 `req_0cc2b71d_xiaomi.json` 的 user msg[0]（对话摘要）中发现了 **3 个 WORKFLOW_HINT**：
```
<!-- WORKFLOW_HINT: 请在下一个请求的 metadata 中设置 "workflow_stage": "execute_solve" -->
<!-- WORKFLOW_HINT: 执行阶段完成。请在下一个请求的 metadata 中设置 "workflow_stage": "analyze_plan" -->
<!-- WORKFLOW_HINT: 分析阶段完成。请在下一个请求的 metadata 中设置 "workflow_stage": "execute_write" -->
```

**结论**：WORKFLOW_HINT 被 Claude Code 保留在对话摘要中，但 CCRG 没有解析它们。

#### 发现二：非摘要请求中没有 WORKFLOW_HINT

在 `req_fae2e92f_xiaomi.json`（540 条消息，无摘要）中搜索 WORKFLOW_HINT，**结果为 0**。

**结论**：当 Claude Code 发送完整对话历史（未 /compact）时，WORKFLOW_HINT 不在 messages 中。

#### 发现三：HTML 注释累计

摘要中的 3 个 hint 来自不同阶段的累积，CCRG 无法确定哪个是"最新"的。

### 3. 对话历史保留情况

| 场景 | 消息数 | 有摘要 | WORKFLOW_HINT 可见 |
|------|--------|--------|-------------------|
| /compact 后 | 31-244 | ✅ | ✅ 在摘要文本中 |
| 未 /compact | 290-542 | ❌ | ❌ 不在 messages 中 |

---

## 可行方案分析

### 方案 A：从摘要中解析 WORKFLOW_HINT（最小侵入）

**原理**：当 CCRG 收到带摘要的请求时，解析 user msg[0] 中的最后一个 WORKFLOW_HINT，提取阶段信息。

**实现**：
```python
def _extract_stage_from_summary(body: dict) -> str | None:
    """从对话摘要中提取最后一个 WORKFLOW_HINT 的阶段信息"""
    messages = body.get("messages", [])
    if not messages:
        return None
    
    first_msg = messages[0]
    if first_msg.get("role") != "user":
        return None
    
    content = first_msg.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text += b.get("text", "")
    
    # 检查是否是摘要
    if "This session is being continued" not in text[:200]:
        return None
    
    # 提取最后一个 WORKFLOW_HINT
    import re
    hints = re.findall(r'WORKFLOW_HINT:.*?workflow_stage.*?"(\w+)"', text)
    if hints:
        return hints[-1]  # 最后一个 hint 是最新的阶段
    return None
```

**优点**：
- 最小侵入，不需要修改 Claude Code
- 利用已有的 WORKFLOW_HINT 注入机制

**缺点**：
- 仅在 /compact 后有效
- 依赖摘要格式稳定性
- 累计 hint 可能导致解析错误

---

### 方案 B：注入可见 Tag 到响应文本（推荐）

**原理**：在 SSE 响应中注入**可见文本** tag（而非 HTML 注释），Claude Code 会自然地将其包含在对话历史中。

**Tag 格式**：
```
[CCRG:STAGE:execute_solve]
```

**实现**：
```python
# 在 workflow_stream_generator 中替换 HTML 注释
if step_name == "intention_analyze":
    tag = "\n\n[CCRG:STAGE:execute_solve]"
    tag_sse = f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': tag}})}\n\n"
    yield tag_sse.encode()
```

**检测逻辑**：
```python
def _extract_stage_from_messages(body: dict) -> str | None:
    """从 messages 中提取 CCRG:STAGE tag"""
    messages = body.get("messages", [])
    
    # 从后往前搜索最后一条 assistant 消息
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                import re
                match = re.search(r'\[CCRG:STAGE:(\w+)\]', text)
                if match:
                    return match.group(1)
        break  # 只检查最后一条 assistant 消息
    return None
```

**优点**：
- 可见文本比 HTML 注释更可靠
- 在完整对话历史和摘要中都能被保留
- 检测逻辑简单

**缺点**：
- 会在响应中显示 tag 文本（用户可见）
- 需要 Claude Code 不过滤自定义 tag

**缓解**：可以将 tag 包裹在不可见字符中，或放在响应末尾。

---

### 方案 C：增强 _infer_stage_from_context（纯推理）

**原理**：不依赖 tag，通过分析消息模式推断阶段。

**当前逻辑**：
- 用户主动输入 → `intention_analyze`
- 工具回调 + last assistant 有 tool_use → `analyze_plan`
- 工具回调 + last assistant 无 tool_use → `execute_solve`

**增强逻辑**：
```python
def _infer_stage_from_context(body: dict) -> str:
    """从 conversation 内容推断当前 workflow 阶段（增强版）"""
    messages = body.get("messages", [])
    
    # 1. 检查是否有 CCRG:STAGE tag（方案 B）
    stage_from_tag = _extract_stage_from_messages(body)
    if stage_from_tag:
        return stage_from_tag
    
    # 2. 检查摘要中的 WORKFLOW_HINT（方案 A）
    stage_from_summary = _extract_stage_from_summary(body)
    if stage_from_summary:
        return stage_from_summary
    
    # 3. 原有推断逻辑
    last_assistant = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg
            break
    
    if not last_assistant:
        return "execute_solve"
    
    # ... 原有 tool_use 检查逻辑
```

**优点**：
- 不需要修改响应格式
- 多层 fallback 机制

**缺点**：
- 推断可能不准确
- 无法区分 analyze_plan 和 execute_write

---

### 方案 D：注入 system-reminder 到 messages

**原理**：CCRG 在转发请求前，向 messages 数组中注入 system-reminder 块。

**实现**：
```python
# 在 workflow_stream_generator 中，转发前注入
ccrg_reminder = {
    "role": "user",
    "content": [{
        "type": "text",
        "text": f"<system-reminder>CCRG workflow stage: {stage}</system-reminder>"
    }]
}
# 插入到 messages 末尾（或倒数第二条之前）
modified_messages = msgs[:-1] + [ccrg_reminder, msgs[-1]]
```

**优点**：
- system-reminder 是 Claude Code 原生支持的机制
- 不会显示给用户

**缺点**：
- Claude Code 可能过滤或修改 system-reminders
- 可能影响 Claude Code 的行为
- 未验证是否会被保留

---

## 推荐方案

### 短期：方案 C（增强推断）+ 方案 A（摘要解析）

1. 优先从摘要中解析 WORKFLOW_HINT（方案 A）
2. 如果没有摘要，使用增强的推断逻辑（方案 C）
3. 保留现有 WORKFLOW_HINT 注入（作为 fallback）

### 中期：方案 B（可见 Tag）

1. 实现可见 tag 注入
2. 验证 Claude Code 是否保留 tag
3. 如果保留，替换方案 A/C

### 长期：方案 D（system-reminder）

1. 验证 system-reminder 是否被保留
2. 如果有效，作为最可靠的机制

---

## 验证步骤

### 步骤 1：验证 WORKFLOW_HINT 在摘要中的可解析性

```python
# 在 main.py 的 workflow_stream_generator 中添加
import re

def _extract_stage_from_summary(body: dict) -> str | None:
    messages = body.get("messages", [])
    if not messages:
        return None
    
    first_msg = messages[0]
    if first_msg.get("role") != "user":
        return None
    
    content = first_msg.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                text += b.get("text", "")
    
    if "This session is being continued" not in text[:200]:
        return None
    
    hints = re.findall(r'WORKFLOW_HINT:.*?workflow_stage.*?"(\w+)"', text)
    return hints[-1] if hints else None
```

### 步骤 2：验证可见 Tag 是否被保留

1. 修改 WORKFLOW_HINT 注入为可见 tag：
   ```python
   tag = "\n\n[CCRG:STAGE:execute_solve]"
   ```
2. 发送测试请求
3. 检查下一个请求的 messages 中是否包含 tag

### 步骤 3：统计验证

在 `D:\Users\viaco\PycharmProjects\minimax-ccr-run\logs\req\` 中搜索：
```bash
grep -l "CCRG:STAGE" *.json
```

如果找到匹配文件，说明 tag 被保留。

---

## 相关文件

- `src/ccrg/main.py` - 核心网关，包含 WORKFLOW_HINT 注入和 stage 检测逻辑
- `src/ccrg/types.py` - WorkflowConfig 定义
- `src/ccrg/splitter/workflow.py` - 工作流意图检测
- `.gateway.json` - 路由配置
- `logs/req/` - 请求日志（dev 环境）
- `logs/req/` (run) - 运行日志（run 环境）

---

## 更新记录

- 2026-08-23: 初始调研，发现 body.metadata 不可用，WORKFLOW_HINT 在摘要中可解析


## 解决方案

### Tag 格式
CCRG 在每个阶段完成后，注入 `{workflow_stage:<next_stage>}` 到响应末尾。

注入映射：
| 当前阶段 | 注入 tag |
|---------|---------|
| intention_analyze | `{workflow_stage:execute_solve}` |
| execute_solve | `{workflow_stage:analyze_plan}` |
| analyze_plan | `{workflow_stage:execute_write}` |

### CCRG 识别逻辑
- 从最后一条 assistant 消息中搜索 `{workflow_stage:xxx}`
- 以最后面的 workflow_stage 为准
- 阶段值：`intention_analyze` / `execute_solve` / `analyze_plan` / `execute_write`

### 已实现
- `_extract_stage_from_tag(body)`: 从 messages 中提取 tag
- `_infer_stage_from_context(body)`: 优先 tag，兜底 tool_use 推断
- 注入逻辑在 `workflow_stream_generator` 中