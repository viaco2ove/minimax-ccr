## llm_splitter

用 LLM 模型判断意图，精度最高但成本也最高。

## 配置

`.gateway.json`：

```json
"splitter": {
    "active_strategy": "llm_splitter",
    "llm_splitter": {
        "routes": ["minimax:MiniMax-M2.7", "qianfan:qianfan-code-latest"],
        "timeout": 10
    }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `routes` | provider:model 列表，按顺序尝试。默认 `["minimax:MiniMax-M2.7"]` |
| `timeout` | 单次 LLM 调用超时（秒），默认 10s |

## 工作原理

1. 从 `keywords.json` 提取 `workflow_intent.chat_intention` 和 `intention_analyze` 关键词作为 few-shot 示例
2. 组装 prompt 发送给 LLM
3. LLM 回复 
```
{
  "workflow_intent": {
    "chat_intention": [
      "咋样"
    ]
}
如果返回的不是josn 直接当作分析失败
```
代表命中了keywords.json的什么内容
4. 调用失败则 fallback 到 `keyword_splitter`

## 提示词的设计
```
你是一个模型分流器。分析命中了哪些关键词？用于为claude code cli 的请求分流
## 数据返回约束，json. 
{
  "workflow_intent": {
    "chat_intention": [
      "咋样"
    ]
}
代表命中了什么

## keywords 数据
{keywords.json}

## 入参说明
一般为xml 格式
# 根节点（固定）
<system-reminder data-role="user-context">
  ├─ <user_info>                # 用户环境信息
  │    ├─ OS Version: win32
  │    ├─ Shell: bash
  │    ├─ Workspace Folder: 路径
  │    └─ Note: 路径使用说明
  │
  ├─ <project_context>          # 项目核心上下文
  │    ├─ <project_guidance>    # 项目规范（CDATA包裹）
  │    │    └─ 项目概述/架构/命令/规则/配置
  │    └─ <project_layout>       # 项目文件目录结构
  │
  ├─ <additional_data>           # 附加数据
  │    ├─ <current_time>         # 当前时间
  │    └─ <connector-status>     # 服务连接状态（全disconnected）
  │
  └─ <memory_and_skills_reminder>  # 记忆&技能规则
       └─ 内存写入/技能管理/通用规则

# 重复嵌套结构（交互轮次）
</system-reminder>
<user_query>用户在cli输入的内容</user_query>

```


## 切换

```bash
# 改 .gateway.json 后重启
python -m src.ccrg.main
```