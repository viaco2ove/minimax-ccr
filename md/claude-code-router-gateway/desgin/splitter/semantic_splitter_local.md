## SemanticSplitter local  本地模型安装和配置
工具推荐： 
- semantic-router
- nomic-embed-text
- BGE-M3
- Ollama+semantic-router
- sentence-transformers
- semchunk
- shibing624/text2vec-base-chinese

模型推荐：
- intfloat/multilingual-e5-small（老牌稳定,老了,但依然能打，比 Qwen3 更快 + 中英都能用 + 质量别太差）
- Qwen/Qwen3-Embedding-0.6B（中英文都强）
- jinaai/jina-embeddings-v3（快，多语言，长文本）
- jinaai/jina-embeddings-v3-small-ci（迷你版）
- BAAI/bge-m3（比较大，速度慢）
- BAAI/bge-small-zh-v1.5（中文为主，如果英文占比超过 20~30%，不太建议）
- moka-ai/m3e-small（中英均衡，使用上如果中文 80%+，速度体验非常好）
- onnx-models/all-MiniLM-L6-v2-onnx（资源极度受限）
- sentence-transformers/all-MiniLM-L6-v2（资源极度受限）
requirements.txt
加入
```
semantic-router
sentence-transformers
wtpsplit
llama-index
```


.gateway.json
```
"splitter": {
      "active_strategy": "semantic_splitter",
      "semantic_splitter": {
          "type": "local",
          "model_name": "shibing624/text2vec-base-chinese"
      }
}
```

# 本地模型配置与支持 
针对 CCRG 意图分流场景（中文为主）：
  推荐: intfloat/multilingual-e5-small（中英文都不错，但是快）
  - 比 Qwen3 更快 + 中英都能用 + 质量别太差
  备选:Qwen/Qwen3-Embedding-0.6B（中英文都强，但是慢）
  备选：BAAI/bge-m3

  - 支持 100+ 语言，中英文都强
  - 体积大一点（~500MB），质量更好
  - 如果后续要处理英文文档或混合语言，用这个

  不推荐：nomic-embed-text — 对中文支持一般。
  不推荐：shibing624/text2vec-base-chinese
  - 专攻中文，体积小（~400MB），速度快,英文不行
  - 适合短文本意图判断（"帮我改个 bug"、"你好"） 

  ---
  对应 semantic_splitter_local.md 配置：
  "semantic_splitter": {
      "type": "local",
      "model_name": "shibing624/text2vec-base-chinese"
  }
  会自动下载模型。    

# 模型安装使用

## 1. 安装依赖

```bash
pip install semantic-router sentence-transformers
```

## 2. 下载模型

```bash
# nomic-embed-text（推荐）
semantic-tool embed --model nomic-embed-text

# 或手动下载
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"
```

## 3. 配置 .gateway.json

```json
"splitter": {
    "active_strategy": "semantic_splitter",
    "semantic_splitter": {
        "type": "local",
        "model_name": "BAAI/bge-m3"
    }
}
```

## 4. 启动验证

```bash
python -m src.ccrg.main
```

看日志有没有报 `Local embedding model loaded` 即可。
