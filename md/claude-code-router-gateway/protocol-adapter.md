---
title: CCRG 协议适配器设计
version: 0.1.0-draft
date: 2026-05-11
---

# 协议适配器设计
protocol:chat_openai/codeplan_anthropic/mmx/codeplan_openai

## 1. 问题

Claude Code 始终以 Anthropic Messages API 格式发请求、期望收到 Anthropic 格式响应。

但上游 Provider 使用不同协议：
- MiniMax / Qianfan: Anthropic 原生格式 → 透传即可
- DeepSeek: OpenAI Chat Completions 格式 → 需要双向转换

Gateway 必须透明地处理这些差异，让 Claude Code 以为一直在和 Anthropic 通信。

## 2. 架构

```
Claude Code (Anthropic 格式)
        │
        ▼
   ┌─────────────┐
   │   Gateway    │
   │             │
   │  ┌─────────────────────────────────────────┐
   │  │          Protocol Adapter               │
   │  │                                         │
   │  │  protocol=anthropic → AnthropicAdapter  │
   │  │    请求: 微调参数 → 透传               │
   │  │    响应: SSE 透传                       │
   │  │                                         │
   │  │  protocol=openai → OpenAIAdapter        │
   │  │    请求: anthropic → openai 转换        │
   │  │    响应: openai → anthropic 转换        │
   │  └─────────────────────────────────────────┘
   │             │
   └─────────────┘
        │
        ▼
   上游 Provider
```

## 3. Adapter 接口

```python
class ProtocolAdapter(ABC):
    """协议适配器基类"""

    @abstractmethod
    async def transform_request(self, request: dict, provider_config: dict) -> dict:
        """将 Anthropic 格式请求转换为目标 Provider 的格式"""
        ...

    @abstractmethod
    async def transform_response_headers(self, headers: dict) -> dict:
        """转换响应头（如 Content-Type）"""
        ...

    @abstractmethod
    async def transform_sse_chunk(self, chunk: bytes, context: dict) -> bytes | None:
        """转换单个 SSE 数据块

        返回:
          - bytes: 转换后的数据块
          - None: 丢弃该数据块（如 OpenAI 的 role chunk）
        """
        ...

    @abstractmethod
    async def transform_json_response(self, response: dict) -> dict:
        """转换非流式 JSON 响应"""
        ...

    @abstractmethod
    def get_target_url(self, provider_config: dict) -> str:
        """获取目标 Provider 的完整 URL"""
        ...
```

## 4. AnthropicAdapter — 透传 + 微调

当 Provider 使用 Anthropic 协议时，几乎不需要转换。但需要处理一些细节：

### 4.1 请求微调

```python
class AnthropicAdapter(ProtocolAdapter):
    async def transform_request(self, request: dict, provider_config: dict) -> dict:
        # 1. 合并 default_params
        default_params = provider_config.get("default_params", {})
        for key, value in default_params.items():
            if key not in request:
                request[key] = value

        # 2. 确保 max_tokens 存在（某些 Provider 要求）
        if "max_tokens" not in request:
            request["max_tokens"] = 4096

        # 3. 清理 system-reminder（某些 Provider 不需要）
        request = self._strip_system_reminders(request)

        return request
```

### 4.2 响应透传

SSE 流和 JSON 响应直接透传，不做转换：

```python
    async def transform_sse_chunk(self, chunk: bytes, context: dict) -> bytes:
        return chunk  # 直接透传

    async def transform_json_response(self, response: dict) -> dict:
        return response  # 直接透传
```

### 4.3 URL 拼接

```python
    def get_target_url(self, provider_config: dict) -> str:
        base = provider_config["api_base_url"].rstrip("/")
        # Anthropic 格式的 Provider，目标路径是 /v1/messages
        if not base.endswith("/v1/messages"):
            base += "/v1/messages"
        return base
```

## 5. OpenAIAdapter — 双向转换

这是最复杂的部分。需要处理：
1. 请求格式转换（Anthropic → OpenAI）
2. 响应格式转换（OpenAI → Anthropic）
3. SSE 流式转换（OpenAI chunk → Anthropic event）
4. tool_use 的双向映射

### 5.1 请求转换 (Anthropic → OpenAI)

```python
class OpenAIAdapter(ProtocolAdapter):

    async def transform_request(self, request: dict, provider_config: dict) -> dict:
        """将 Anthropic Messages 请求转换为 OpenAI Chat Completions 请求"""

        # ── system prompt ──
        system = self._extract_system(request)

        # ── messages 转换 ──
        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        for msg in request.get("messages", []):
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                messages.append(self._convert_user_message(msg))
            elif role == "assistant":
                messages.append(self._convert_assistant_message(msg))
            # tool 角色在 OpenAI 中不存在，需要特殊处理

        # ── tools 转换 ──
        tools = self._convert_tools(request.get("tools", []))

        # ── 构建请求 ──
        oai_request = {
            "model": request.get("model", provider_config["models"][0]),
            "messages": messages,
            "stream": request.get("stream", False),
        }

        if tools:
            oai_request["tools"] = tools

        # ── 参数映射 ──
        if "max_tokens" in request:
            oai_request["max_tokens"] = request["max_tokens"]
        if "temperature" in request:
            oai_request["temperature"] = request["temperature"]
        if "stop_sequences" in request:
            oai_request["stop"] = request["stop_sequences"]

        # ── thinking → reasoning ──
        thinking = request.get("thinking")
        if thinking and thinking.get("type") == "enabled":
            oai_request["reasoning_effort"] = "high"

        # ── 合并 default_params ──
        default_params = provider_config.get("default_params", {})
        for key, value in default_params.items():
            if key not in oai_request:
                oai_request[key] = value

        return oai_request
```

### 5.2 消息格式转换细节

```python
    def _convert_user_message(self, msg: dict) -> dict:
        """Anthropic user message → OpenAI user message"""
        content = msg.get("content", "")

        if isinstance(content, str):
            return {"role": "user", "content": content}

        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")

                    if block_type == "text":
                        parts.append({"type": "text", "text": block.get("text", "")})

                    elif block_type == "image":
                        # Anthropic image → OpenAI image_url
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            media_type = source.get("media_type", "image/png")
                            data = source.get("data", "")
                            parts.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{data}"
                                }
                            })
                        elif source.get("type") == "url":
                            parts.append({
                                "type": "image_url",
                                "image_url": {"url": source.get("url", "")}
                            })

                    elif block_type == "tool_result":
                        # Anthropic tool_result → OpenAI tool role
                        # 这个在 _convert_messages 中单独处理
                        tool_id = block.get("tool_use_id", "")
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = " ".join(
                                b.get("text", "") for b in result_content if isinstance(b, dict)
                            )
                        return {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": str(result_content)
                        }

            return {"role": "user", "content": parts}

        return {"role": "user", "content": str(content)}

    def _convert_assistant_message(self, msg: dict) -> dict:
        """Anthropic assistant message → OpenAI assistant message"""
        content = msg.get("content", "")
        result = {"role": "assistant"}

        if isinstance(content, str):
            result["content"] = content
            return result

        if isinstance(content, list):
            text_parts = []
            tool_calls = []

            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")

                    if block_type == "text":
                        text_parts.append(block.get("text", ""))

                    elif block_type == "tool_use":
                        # Anthropic tool_use → OpenAI tool_calls
                        tool_calls.append({
                            "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)
                            }
                        })

                    elif block_type == "thinking":
                        # thinking 块暂不转换（OpenAI 没有 thinking 块）
                        pass

            result["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                result["tool_calls"] = tool_calls

        return result

    def _convert_tools(self, anthropic_tools: list) -> list:
        """Anthropic tool 定义 → OpenAI function 定义"""
        if not anthropic_tools:
            return []

        oai_tools = []
        for tool in anthropic_tools:
            if tool.get("type") == "custom" or "name" in tool:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {})
                    }
                })

        return oai_tools
```

### 5.3 非流式响应转换 (OpenAI → Anthropic)

```python
    async def transform_json_response(self, response: dict) -> dict:
        """将 OpenAI Chat Completions 响应转换为 Anthropic Messages 响应"""

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        content_blocks = []

        # ── thinking/reasoning → thinking block ──
        reasoning = message.get("reasoning_content")
        if reasoning:
            content_blocks.append({
                "type": "thinking",
                "thinking": reasoning
            })

        # ── text content ──
        text = message.get("content", "")
        if text:
            content_blocks.append({
                "type": "text",
                "text": text
            })

        # ── tool_calls → tool_use blocks ──
        for tc in message.get("tool_calls", []):
            fn = tc.get("function", {})
            try:
                input_data = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                input_data = {"raw_arguments": fn.get("arguments", "")}

            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
                "name": fn.get("name", ""),
                "input": input_data
            })

        # ── stop_reason 映射 ──
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(finish_reason, "end_turn")

        # ── usage 映射 ──
        usage = response.get("usage", {})

        return {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": response.get("model", ""),
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "cache_read_input_tokens": 0
            }
        }
```

### 5.4 流式 SSE 转换 (OpenAI chunk → Anthropic event)

这是最难的部分。需要在两个不同的 SSE 事件格式之间实时转换。

**OpenAI SSE 格式:**
```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"tool_calls":[...]},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

**Anthropic SSE 格式:**
```
event: message_start
data: {"type":"message_start","message":{...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}

event: message_stop
data: {"type":"message_stop"}
```

```python
class OpenAISSEConverter:
    """OpenAI SSE → Anthropic SSE 实时转换器"""

    def __init__(self, model: str):
        self.model = model
        self.msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.content_block_index = 0
        self.in_tool_call = False
        self.tool_call_index = 0
        self.started = False
        self.output_tokens = 0

    def convert_chunk(self, raw_chunk: bytes) -> list[bytes]:
        """将一个 OpenAI SSE chunk 转换为多个 Anthropic SSE events

        返回列表因为一个 OpenAI chunk 可能产生多个 Anthropic events
        """
        line = raw_chunk.decode("utf-8", errors="replace").strip()

        # 跳过空行
        if not line:
            return []

        # 跳过非 data 行
        if not line.startswith("data: "):
            return []

        data = line[6:]  # 去掉 "data: "

        # [DONE] 信号
        if data.strip() == "[DONE]":
            return self._build_message_stop()

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return []

        choice = chunk.get("choices", [{}])[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        events = []

        # ── 首个 chunk: 发送 message_start + content_block_start ──
        if not self.started:
            self.started = True
            events.append(self._build_message_start())
            events.append(self._build_content_block_start("text"))

        # ── role chunk: 跳过 ──
        if "role" in delta and not delta.get("content") and not delta.get("tool_calls"):
            return events

        # ── reasoning_content → thinking block ──
        if delta.get("reasoning_content"):
            # 如果当前在 text block 中，先关闭
            if not self.in_tool_call and self.content_block_index > 0:
                # 已经在 text block 中，需要处理
                pass
            # 发送 thinking delta（简化处理：当作 text 发送）
            # TODO: 正式实现需要支持 thinking block
            events.append(self._build_text_delta(delta["reasoning_content"]))

        # ── content → text_delta ──
        if delta.get("content"):
            self.output_tokens += len(delta["content"]) // 4  # 粗略估算
            events.append(self._build_text_delta(delta["content"]))

        # ── tool_calls → tool_use blocks ──
        if delta.get("tool_calls"):
            for tc_delta in delta["tool_calls"]:
                tc_events = self._handle_tool_call_delta(tc_delta, events)
                events.extend(tc_events)

        # ── finish_reason → content_block_stop + message_delta ──
        if finish_reason:
            events.extend(self._build_finish(finish_reason))

        return events

    def _build_message_start(self) -> bytes:
        msg_start = {
            "type": "message_start",
            "message": {
                "id": self.msg_id, "type": "message", "role": "assistant",
                "model": self.model, "content": [], "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        }
        return f"event: message_start\ndata: {json.dumps(msg_start)}\n\n".encode("utf-8")

    def _build_content_block_start(self, block_type: str, name: str = "", block_id: str = "") -> bytes:
        content_block = {"type": block_type, "text": ""} if block_type == "text" else {"type": block_type, "id": block_id, "name": name, "input": {}}
        event = {
            "type": "content_block_start",
            "index": self.content_block_index,
            "content_block": content_block
        }
        return f"event: content_block_start\ndata: {json.dumps(event)}\n\n".encode("utf-8")

    def _build_text_delta(self, text: str) -> bytes:
        delta = {
            "type": "content_block_delta",
            "index": self.content_block_index,
            "delta": {"type": "text_delta", "text": text}
        }
        return f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n".encode("utf-8")

    def _build_content_block_stop(self) -> bytes:
        event = {"type": "content_block_stop", "index": self.content_block_index}
        return f"event: content_block_stop\ndata: {json.dumps(event)}\n\n".encode("utf-8")

    def _build_finish(self, finish_reason: str) -> list[bytes]:
        events = []

        # 关闭当前 content block
        events.append(self._build_content_block_stop())

        # stop_reason 映射
        stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
        stop_reason = stop_map.get(finish_reason, "end_turn")

        # message_delta
        msg_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self.output_tokens}
        }
        events.append(f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n".encode("utf-8"))

        # message_stop
        msg_stop = {"type": "message_stop"}
        events.append(f"event: message_stop\ndata: {json.dumps(msg_stop)}\n\n".encode("utf-8"))

        return events

    def _build_message_stop(self) -> list[bytes]:
        if self.started:
            return []  # 已经在 finish 中发送了
        # 如果没有收到过任何 chunk 就收到 [DONE]
        msg_start = self._build_message_start()
        content_start = self._build_content_block_start("text")
        text_delta = self._build_text_delta("")
        content_stop = self._build_content_block_stop()
        msg_delta = f'event: message_delta\ndata: {json.dumps({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":0}})}\n\n'.encode("utf-8")
        msg_stop = f'event: message_stop\ndata: {json.dumps({"type":"message_stop"})}\n\n'.encode("utf-8")
        return [msg_start, content_start, text_delta, content_stop, msg_delta, msg_stop]

    def _handle_tool_call_delta(self, tc_delta: dict, current_events: list) -> list[bytes]:
        """处理 tool_calls 的增量更新"""
        events = []

        # 新 tool call 开始
        if tc_delta.get("id"):
            # 关闭之前的 text block
            if not self.in_tool_call:
                events.append(self._build_content_block_stop())
                self.content_block_index += 1

            self.in_tool_call = True
            self.tool_call_index = self.content_block_index

            fn = tc_delta.get("function", {})
            block_id = tc_delta.get("id", f"toolu_{uuid.uuid4().hex[:12]}")
            events.append(self._build_content_block_start("tool_use", fn.get("name", ""), block_id))

        # tool call 参数增量
        if tc_delta.get("function", {}).get("arguments"):
            arg_delta = tc_delta["function"]["arguments"]
            delta = {
                "type": "content_block_delta",
                "index": self.content_block_index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": arg_delta
                }
            }
            events.append(f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n".encode("utf-8"))

        return events
```

### 5.5 URL 拼接

```python
    def get_target_url(self, provider_config: dict) -> str:
        base = provider_config["api_base_url"].rstrip("/")
        # OpenAI 格式的 Provider，目标路径是 /v1/chat/completions
        if not base.endswith("/v1/chat/completions"):
            base += "/v1/chat/completions"
        return base
```

## 6. Streaming 代理

Gateway 需要实现一个 streaming 代理，能够：
1. 接收上游 Provider 的 SSE 流
2. 实时转换每个 chunk
3. 转发给 Claude Code

```python
async def stream_response(self, response: httpx.Response, adapter: ProtocolAdapter, send_event: Callable):
    """流式代理：读取上游 SSE → 转换 → 转发"""
    converter = OpenAISSEConverter(model="") if isinstance(adapter, OpenAIAdapter) else None

    async for line in response.aiter_lines():
        if not line.strip():
            continue

        if isinstance(adapter, AnthropicAdapter):
            # Anthropic 格式直接转发
            await send_event(line.encode("utf-8") + b"\n\n")

        elif isinstance(adapter, OpenAIAdapter) and converter:
            # OpenAI 格式需要转换
            events = converter.convert_chunk(line.encode("utf-8"))
            for event in events:
                await send_event(event)
```

## 7. CCR 兼容的细节处理

从 CCR 源码中学到的关键细节：

### 7.1 system prompt 处理

Anthropic 的 `system` 字段可以是 string 或 array。OpenAI 只有 string 格式的 system message。

```python
def _extract_system(self, request: dict) -> str:
    system = request.get("system", "")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return ""
```

### 7.2 缓存控制标记

Anthropic 有 `cache_control` 标记，OpenAI 没有。转换时需要剥离：

```python
def _strip_cache_control(self, obj: dict | list) -> dict | list:
    """移除 cache_control 字段（OpenAI 不支持）"""
    if isinstance(obj, dict):
        return {k: self._strip_cache_control(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [self._strip_cache_control(item) for item in obj]
    return obj
```

### 7.3 多模态内容处理

Anthropic 和 OpenAI 的图片格式不同：

| | Anthropic | OpenAI |
|---|-----------|--------|
| base64 | `{"type":"image","source":{"type":"base64","media_type":"...","data":"..."}}` | `{"type":"image_url","image_url":{"url":"data:...;base64,..."}}` |
| URL | `{"type":"image","source":{"type":"url","url":"..."}}` | `{"type":"image_url","image_url":{"url":"..."}}` |

### 7.4 tool_choice 映射

| Anthropic | OpenAI |
|-----------|--------|
| `{"type":"auto"}` | `{"type":"auto"}` |
| `{"type":"any"}` | `{"type":"required"}` |
| `{"type":"tool","name":"X"}` | `{"type":"function","function":{"name":"X"}}` |
| _(缺失)_ | `{"type":"none"}` |

### 7.5 Bypass 模式

当 Provider 的 protocol 与 Claude Code 的请求格式相同时（都是 anthropic），Gateway 可以跳过所有转换，直接透传请求和响应。这比 CCR 的 bypass 检测更简单 — 直接看 `protocol` 字段即可。

## 8. 错误处理

### 8.1 Provider 返回错误

```python
class ProviderError(Exception):
    def __init__(self, status_code: int, body: dict, provider: str):
        self.status_code = status_code
        self.body = body
        self.provider = provider

def _convert_error_to_anthropic(self, error: ProviderError) -> dict:
    """将 Provider 错误转换为 Anthropic 格式的错误响应"""
    if isinstance(error.body, dict) and "error" in error.body:
        # OpenAI 格式: {"error": {"message": "...", "type": "..."}}
        oai_error = error.body["error"]
        return {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": oai_error.get("message", str(error.body))
            }
        }

    return {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": str(error.body)
        }
    }
```

### 8.2 转换失败

如果请求/响应转换过程中出错，不要吞掉错误，记录日志并返回 500：

```python
try:
    transformed = await adapter.transform_request(request, provider_config)
except Exception as e:
    logger.error(f"Request transform failed: {e}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"type": "error", "error": {"type": "api_error", "message": f"Gateway transform error: {e}"}}
    )
```

## 9. 已知限制

1. **thinking 块转换不完整** — OpenAI 的 reasoning_content 和 Anthropic 的 thinking block 格式差异较大，初期简化处理
2. **tool_use 增量解析** — OpenAI 的 tool_calls arguments 是 JSON 字符串的增量片段，需要拼接后解析，边界情况较多
3. **多模态限制** — video/audio 类型的内容暂不支持转换
4. **token 计数不精确** — 转换后的请求 token 数与原始请求不同，usage 数据仅供参考
5. **SSE 事件类型** — Anthropic 有 `event:` 前缀，OpenAI 没有，需要注意格式差异
