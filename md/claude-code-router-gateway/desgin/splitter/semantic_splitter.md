# SemanticSplitter 设计文档

基于语义向量相似度的工作流意图分流器。

## 工作原理

将用户输入和预定义的意图候选（chat/task description）分别转为 embedding 向量，通过余弦相似度判断意图。

```
用户消息 → embedding API → 向量 A
候选描述 → embedding API → 向量 B
                          ↓
                  余弦相似度计算
                          ↓
                    意图分类 (chat/task)
```

## 配置

在 `.gateway.json` 的 `routing.splitter` 下配置：

```json
{
  "routing": {
    "splitter": {
      "active_strategy": "semantic_splitter",
      "semantic_splitter": {
        "embedding_provider": "minimax",
        "embedding_model": "embo-01",
        "embedding_api": "https://api.minimaxi.com/embedding",
        "embedding_api_key": "$EMBEDDING_API_KEY"
        "threshold": 0.6
      }
    }
  }
}
```

### 配置字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `embedding_provider` | string | 是 | embedding provider 名称（用于日志标识） |
| `embedding_model` | string | 是 | embedding 模型名 |
| `embedding_api` | string | 是 | embedding API 完整 URL |
| `embedding_api_key` | string | 是 | API 密钥（支持 `$VAR` 环境变量） |
| `candidates` | array | 否 | 意图候选列表，默认包含 task/chat |
| `threshold` | float | 否 | 相似度阈值，默认 0.6。低于此值则回退到 keyword_splitter |

## API 响应格式

当前支持两种 embedding API 响应格式：

**格式 1（MiniMax 等）**
```json
{
  "data": [
    {
      "embedding": [0.123, -0.456, ...]
    }
  ]
}
```

**格式 2（OpenAI 兼容）**
```json
{
  "embeddings": [
    [0.123, -0.456, ...]
  ]
}
```

如果 API 响应格式不同，需要在 `SemanticSplitter._get_embedding()` 中适配。

## 阈值说明

- `threshold` 设置为 0.6 时：
  - 用户输入与 task/chat 候选的相似度都低于 0.6 → 回退到 `keyword_splitter`
  - 相似度最高者 ≥ threshold → 采用该意图

阈值建议范围 0.5~0.7，可根据实际命中率调整。

## 回退机制

1. **embedding API 调用失败** → 回退到 `keyword_splitter`
2. **相似度低于 threshold** → 回退到 `keyword_splitter`
3. **用户消息为空** → 回退到 `keyword_splitter`

回退时不会重新调用 LLM，直接使用 `KeywordSplitter` 的检测结果。

## 性能考量

- 每次意图检测需要调用 3 次 embedding API（1 次用户输入 + 2 次候选描述）
- 可考虑在配置中预计算并缓存候选 embedding（TODO）
- embedding API 建议 timeout 设置 30s 以内

## 切换流程

```bash
# 1. 修改 .gateway.json
# 将 active_strategy 从 "keyword_splitter" 改为 "semantic_splitter"

# 2. 重启 CCRG
python -m src.ccrg.main

# 3. 观察日志中的意图检测结果
# 应该有类似输出：
# SemanticSplitter: intent scores: {'task': 0.73, 'chat': 0.21}
# SemanticSplitter matched intent=task (score=0.73)
```

## 调试

启用 debug 日志可以看到详细判断过程：

```json
{
  "TRANSLATOR_VERBOSE": true
}
```

agent 设计 类似llm 分流器
[llm_splitter.md](llm_splitter.md)