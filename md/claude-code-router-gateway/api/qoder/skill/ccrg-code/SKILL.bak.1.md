---
name: ccrg-code
description: 通过 CCRG MCP 工具执行多步复杂编程任务（读文件、写代码、执行命令、分析需求、代码审查）。当用户提及 @ccrg_code、CCRG、或需要将复杂编程任务委托给外部 LLM 时使用此技能。
---

# CCRG Code - 多步复杂编程工具

通过 `ccrg_code` MCP 工具将复杂编程任务委托给 CCRG LLM 执行。支持读取文件、写入代码、执行命令、规划任务、代码审查等多种动作。

## MCP 调用规则

### ccrg 不可用时，要反馈给用户，不允许自己跳过mcp 服务!!!

### ccrg_code 超时了。不要自己进行下一步操作。要告诉用户超时。等待用户反馈

### 上下文
要发送上下文发送给ccrg mcp 服务
要把当前项目地址发送给ccrg mcp 服务
要把涉及文件和代码片段发送给ccrg mcp 服务

### 分步调用
一次调用技能。多次与ccrg_code 进行交互。实现多步复杂编程流程 实现完整ai agent 能力
- 分析文件
- 查找文件
- 分析问题
- 处理问题

## MCP 工具调用方式

```
Server: ccrg
Tool:   ccrg_code
```

## 核心参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `task_type` | 是 | 动作类型：`read` / `write` / `exec` / `plan` / `review` / `chat` |
| `task` | 是 | 任务描述（自然语言） |
| `files` | 否 | 相关文件路径列表（用于 read/review） |
| `file_contents` | 否 | 文件内容数组 `[{path, content}]`（用于 write） |
| `commands` | 否 | 命令列表（用于 exec） |
| `context` | 否 | 额外上下文（项目结构、错误日志等） |
| `model` | 否 | 指定模型（留空自动路由） |

## task_type 选择指南

| task_type | 适用场景 | 关键参数 |
|-----------|---------|---------|
| `read` | 分析/理解代码文件 | `files` |
| `write` | 生成或修改代码 | `file_contents`, `context` |
| `exec` | 运行脚本/命令 | `commands` |
| `plan` | 设计实现方案 | `context` |
| `review` | 审查代码质量 | `files` 或 `file_contents` |
| `chat` | 问答/讨论 | `context` |

## 多步工作流模式

对于复杂任务，按以下步骤编排多次调用：

### Step 1: 分析（read）
```json
{
  "task_type": "read",
  "task": "分析以下文件的结构和字段映射关系",
  "files": ["path/to/file1.ktr", "path/to/file2.xlsx"]
}
```

### Step 2: 规划（plan）
```json
{
  "task_type": "plan",
  "task": "根据分析结果，制定修改方案",
  "context": "<step1 的返回结果>"
}
```

### Step 3: 执行（write / exec）
```json
{
  "task_type": "write",
  "task": "按照方案修改文件",
  "file_contents": [{"path": "...", "content": "..."}],
  "context": "<step2 的规划结果>"
}
```

### Step 4: 验证（exec）
```json
{
  "task_type": "exec",
  "task": "验证修改结果",
  "commands": ["python -c \"import xml.etree.ElementTree as ET; ET.parse('file.ktr'); print('ok')\""]
}
```

## 调用示例

### 示例 1：读取并分析 Kettle 转换文件
```
CallMcpTool(server="ccrg", tool="ccrg_code", arguments={
  "task_type": "read",
  "task": "提取 T14_migrate_chat_frame.ktr 中所有 InsertUpdate 步骤的目标字段映射",
  "files": ["script/kettle/business_job/J03/T14_migrate_chat_frame.ktr"]
})
```

### 示例 2：生成修正脚本
```
CallMcpTool(server="ccrg", tool="ccrg_code", arguments={
  "task_type": "write",
  "task": "根据字段映射生成 Python openpyxl 脚本，修正 data_map.xlsx",
  "file_contents": [{"path": "fix.py", "content": "..."}],
  "context": "映射关系: source_id->id(偏移), fromno->clientele_id(DBLookup)..."
})
```

### 示例 3：执行验证
```
CallMcpTool(server="ccrg", tool="ccrg_code", arguments={
  "task_type": "exec",
  "task": "运行修正脚本并验证 xlsx 更新结果",
  "commands": ["python fix_data_map.py"]
})
```

## 注意事项

1. **链式调用时传递上下文**：将上一步返回的 `text` 字段作为下一步的 `context` 传入
2. **文件路径使用相对路径**：相对于项目根目录
3. **write 操作需要完整内容**：`file_contents` 中必须提供完整的文件内容
4. **exec 命令以列表形式传入**：每个命令一个字符串元素
5. **大文件分批处理**：如果文件过大，先用 `read` 分析关键部分，再针对性处理
