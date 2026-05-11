取代claude-code-router （ccr） 
# 写一个很薄的 Router。

---

你现在这个需求：

```txt id="v9jfgf"
Claude Code
→ 部分请求走 MiniMax 套餐
→ 部分请求走 DeepSeek/Claude
```

目前：

# 没有成熟工具能完美做。

CCR 也不行。

LiteLLM 也不行。

因为它们都默认：

```txt id="fczt7z"
“一次会话固定模型”
```

而你要的是：

# 同一个 agent loop 内动态切模型。

---

但好消息是：

# 这东西真的不复杂。

---

# 最小实现甚至可能：

## 一个 express / fastapi 文件。

逻辑：

```python id="hmdb0n"
@app.post("/v1/messages")
async def route(req):

    text = extract_text(req)

    if is_cheap_task(text):
        return minimax(req)

    else:
        return deepseek(req)
```

---

# 关键：

Claude Code 根本不知道。

它只会认为：

```txt id="jq5a5r"
自己一直在调用 Anthropic
```

---

# 真正麻烦的地方只有两个

## 1. streaming

Anthropic SSE 格式。

---

## 2. tool_use 转发

但：

# 其实 CCR 源码已经帮你证明：

这些并不复杂。

---

# 你甚至不用从零写

最现实的是：

## fork CCR

然后：

# 加一个 request classifier。

---

例如：

```python id="7r4k1w"
if "search" in prompt:
   provider = minimax

if "analyze" in prompt:
   provider = deepseek
```

---

# 工作量现实估计

| 方案         | 工作量   |
| ---------- | ----- |
| 从零写        | 2~3天  |
| fork CCR 改 | 半天~1天 |
| 只做关键词路由    | 几小时   |

---

# 其实你现在已经找到了：

## 真正有价值的方向

不是：

```txt id="brjpfz"
“再套一层 API”
```

而是：

# “Agent 请求分类”

---

因为 Claude Code 里：

## 最耗钱的请求：

其实根本不需要高智商。

比如：

* read file
* summarize
* grep
* explain function

这些 MiniMax 完全能干。

---

真正需要 Claude/DeepSeek 的：

才是：

* cross-file reasoning
* architecture
* difficult debugging
* patch planning

---

# 所以你现在不是“要不要自己写”

而是：

# “值不值得写”

我觉得：

# 很值得。

因为：

## 你这个需求非常真实。

很多人都有。

但现有工具都没解决好。
