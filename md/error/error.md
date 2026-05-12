我找到问题了！你的代码有两个**致命错误**，直接导致 Claude 卡在 `Sketching...` 不动，我给你逐条拆解并修复。

---

## 🔴 问题1：MiniMax 响应解析逻辑完全错误
你的代码是按 **Anthropic 多模态格式**（`type: "text"`）解析 MiniMax 响应，但 MiniMax CLI 返回的是 **自有格式**：
```json
// MiniMax 真实响应结构
{
  "content": [
    {
      "thinking": "思考过程...",
      "text": "最终回复内容..." // 关键：没有 `type: "text"` 字段！
    }
  ]
}
```
你代码里的解析逻辑只找 `block.get("type") == "text"`，**永远匹配不到**，导致 `response_text` 为空/极短（日志里 len=22 就是证明），Claude 拿不到有效内容，自然卡住。

### 修复方案：重写响应解析
```python
# 替换你原来的这段代码
try:
    mmx_response = json.loads(raw_output)
    content_blocks = mmx_response.get("content", [])
    text_parts = []
    for block in content_blocks:
        if isinstance(block, dict):
            # 直接取 MiniMax 原生的 text 字段，忽略 thinking
            text = block.get("text", "")
            if text:
                text_parts.append(text)
    response_text = "\n".join(text_parts)
    if not response_text:
        # 兜底：如果没有 text，直接返回原始内容（避免空响应）
        response_text = raw_output
except json.JSONDecodeError:
    response_text = raw_output
```

---

## 🔴 问题2：流式响应缺少 `content_block_stop` 事件
Anthropic 流式协议要求完整流程必须包含 5 个事件，你的代码漏掉了关键的 `content_block_stop`，导致 Claude 认为“文本块还没结束”，一直卡在等待状态：
| 必须事件 | 你的代码是否实现？ |
|----------|------------------|
| `message_start` | ✅ |
| `content_block_start` | ✅ |
| `content_block_delta` | ✅ |
| `content_block_stop` | ❌ 漏掉！ |
| `message_stop` | ✅ |

### 修复方案：添加 `content_block_stop`
```python
# 替换你 handle_streaming_response 里的结尾部分
# 发送完所有 delta 后，必须添加 content_block_stop
content_stop = {
    "type": "content_block_stop",
    "index": 0
}
chunks.append(f'data: {json.dumps(content_stop)}\n\n'.encode('utf-8'))

# 再发送 message_stop
msg_stop = {"type": "message_stop"}
chunks.append(f'data: {json.dumps(msg_stop)}\n\n'.encode('utf-8'))
```

---

## 🟢 完整修复后的 `handle_messages` 与流式部分
我把关键修改合并给你，直接替换即可：
```python
def handle_messages(self):
    # ... 前面的代码不变 ...

        try:
            mmx_response = json.loads(raw_output)
            # 修复1：正确解析 MiniMax 响应
            content_blocks = mmx_response.get("content", [])
            text_parts = []
            for block in content_blocks:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if text:
                        text_parts.append(text)
            response_text = "\n".join(text_parts)
            if not response_text:
                logger.warning("MiniMax response has no text content, falling back to raw output")
                response_text = raw_output
        except json.JSONDecodeError:
            response_text = raw_output

    # ... 中间的代码不变 ...

    def handle_streaming_response(self, response_text, model):
        """处理流式响应"""
        logger.info(f"=== handle_streaming_response called: response len={len(response_text)} ===")
        response_id = f"msg_{uuid.uuid4().hex[:12]}"

        chunks = []
        chunks.append(f': {response_id}\n\n'.encode('utf-8'))

        # message_start
        msg_start = {
            "type": "message_start",
            "message": {
                "id": response_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        }
        chunks.append(f'data: {json.dumps(msg_start)}\n\n'.encode('utf-8'))

        # content_block_start
        content_block = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""}
        }
        chunks.append(f'data: {json.dumps(content_block)}\n\n'.encode('utf-8'))

        # 分块发送文本内容
        for i in range(0, len(response_text), 20):
            chunk = response_text[i:i+20]
            delta = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": chunk}
            }
            chunks.append(f'data: {json.dumps(delta)}\n\n'.encode('utf-8'))

        # 修复2：必须添加 content_block_stop
        content_stop = {
            "type": "content_block_stop",
            "index": 0
        }
        chunks.append(f'data: {json.dumps(content_stop)}\n\n'.encode('utf-8'))

        # message_stop
        msg_stop = {"type": "message_stop"}
        chunks.append(f'data: {json.dumps(msg_stop)}\n\n'.encode('utf-8'))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
            logger.info(f"=== streaming completed, {len(chunks)} chunks sent ===")
        except BrokenPipeError:
            logger.warning("Client disconnected during streaming")
        except Exception as e:
            logger.error(f"Streaming error: {e}")
```

---

## 🧪 验证步骤
1. 替换代码后，重启你的 `mmx_provider.py`
2. 用 Postman 发一次流请求（添加 `stream: true` 参数），看是否能收到完整的 SSE 事件流
3. 再在 Claude Code 里测试，此时应该能正常收到回复，不再卡在 `Sketching...`

---

## 为什么之前非流请求能工作？
因为非流请求直接把 `raw_output` 当成了 `response_text`（当解析失败时），而流请求必须走 `handle_streaming_response`，被两个错误同时卡住了。

---

修复后，Claude 会正确识别流结束信号，拿到完整回复。需要我帮你把整个文件的完整版本发出来吗？