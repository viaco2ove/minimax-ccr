# 分流模块
src/ccrg/splitter
根据关键词等对请求进行 模型分流

## 分流机制 -V1
目前有三层分流机制：
### 系统分流与路由机制说明

#### 一、 三层分流机制

*控制业务流程所处的阶段，按以下优先级自上而下匹配：*

| 分流层 | 触发条件 | 依据 |
| --- | --- | --- |
| **Agent CLI metadata** | `metadata.workflow_stage` 有值 | CLI 直接指定 |
| **关键词检测** | metadata 无值时 | `keywords.json` 单词边界匹配 |
| **意图推断** | 关键词都没命中时 | 是否为用户主动发起 |

---

#### 二、 Provider 路由 (`router/engine.py`)

*另一套独立的底层路由系统，用于决定最终的模型或服务提供商：*

| 路由策略 | 依据 |
| --- | --- |
| **scenario** | 场景分类器 |
| **tool_routing** | 工具类型分类器 |
| **keyword_routing** | 关键词分类器 |
| **default** | 兜底机制 |
| **capabilities 检查** | `thinking` / `vision` / `tool_use` 能力匹配 |

---

#### 💡 核心结论

> **整个系统的路由主要依靠“关键词 + 关键词分类”来实现。**
> Agent CLI 的主要作用是覆盖 `workflow_stage` 来参与前置的“分流”，它**不参与**后置的 Provider 路由决策。

## 分流机制-V2
### 分流器
.gateway.json 中配置splitter
- keyword_splitter:就是跟V1 一样的机制的分流器,默认的分流器
- semantic_splitter:语义向量 semantic-router 取代keyword_routing
- llm_splitter:用ai模型实现分流 llm_router取代keyword_routing

```
"splitter": {
      "active_strategy": "llm_splitter",
      "llm_splitter":["minimax:MiniMax-M2.7","qianfan:qianfan-code-latest"]
}
```
```
    "splitter": {
          "active_strategy": "semantic_splitter",
          "semantic_splitter": {
              "type": "local",
              "model_name": "moka-ai/m3e-small"
          }
    }
```
```
    "splitter": {
      "active_strategy": "llm_splitter",
      "llm_splitter": {
        "routes": ["local:Qwen3-0.6B", "minimax:MiniMax-M2.7"]
      },
      "local_model": {
        "model_name": "Qwen3-0.6B",
        "local_chat_model_run_start": true,
        "gguf_file_name": "Qwen3-0.6B-Q8_0.gguf",
        "local_dir": "local_models",
        "model_scope_url": "https://www.modelscope.cn/models/Qwen/Qwen3-0.6B-GGUF/resolve/master/Qwen3-0.6B-Q8_0.gguf",
        "hf_mirror_url": "https://hf-mirror.com/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf",
        "hf_url": "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf",
        "download_headers": {
          "Cookie": "acw_tc=2f5c8ddc17816851321176544ec8ed3cd99ef88b714a184983656b35bae620; csrf_session=MTc4MTY4NTEzMnxEWDhFQVFMX2dBQUJFQUVRQUFBeV80QUFBUVp6ZEhKcGJtY01DZ0FJWTNOeVpsTmhiSFFHYzNSeWFXNW5EQklBRUVkb1kwMXJjRzQ1Ym5vM1p6RjJSemc9fIU0nt9VkelyDhMfF9E6el3ZZ-qMuV30zsyzz4jV_Rwv; csrf_token=S9Tvc8kD-yOrXx6l_n09-ooBgVY%3D"
        },
        "n_ctx": 15868,
        "n_threads": 4,
        "n_threads_batch": 4,
        "n_batch": 512,
        "use_mmap": true,
        "mlock": false,
        "max_tokens": 512
      },
      "semantic_splitter": {
          "type": "local",
          "model_name": "moka-ai/m3e-small",
          "hf_endpoint":"https://hf-mirror.com",
          "trust_remote_code": true
      }
    }
```

active_strategy 选择哪一种分流器.默认是keyword_splitter
llm_splitter 配置那个providers用于llm_splitter

## keyword_splitter vs semantic_splitter_local vs llm_splitter
[splitter_vs.md](splitter_vs.md)