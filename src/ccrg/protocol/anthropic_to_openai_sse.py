"""
Anthropic SSE → OpenAI SSE 实时转换器。

将 Anthropic Messages 格式的 SSE 流转换为 OpenAI Chat Completions 格式。
"""

import json
import uuid
from typing import AsyncGenerator


class AnthropicToOpenAISSEConverter:
    """Anthropic SSE → OpenAI SSE 实时转换器

    将 Anthropic SSE 格式的流式响应转换为 OpenAI Chat Completions 格式。
    """

    def __init__(self, model: str = ""):
        self.model = model
        self.chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self.created = 0  # 会设置为当前时间戳
        self.content_block_index = 0
        self.in_tool_call = False
        self.started = False
        self.role_sent = False
        self.output_tokens = 0
        self.input_tokens = 0
        self._pending_event_type: str | None = None
        self._current_tool_call_id: str | None = None
        self._current_tool_name: str | None = None
        self._current_tool_args: str = ""

    def convert_chunk(self, raw_chunk: bytes) -> list[bytes]:
        """将一个 Anthropic SSE chunk 转换为 OpenAI SSE chunk

        返回列表因为一个 Anthropic chunk 可能产生多个 OpenAI chunks
        """
        line = raw_chunk.decode("utf-8", errors="replace").strip()

        # 跳过空行
        if not line:
            return []

        # 处理 event: 行（缓存以供下一个 data: 行使用）
        if line.startswith("event: "):
            self._pending_event_type = line[7:].strip()
            return []

        # 跳过非 data 行
        if not line.startswith("data: "):
            return []

        data = line[6:]  # 去掉 "data: "

        # [DONE] 信号
        if data.strip() == "[DONE]":
            return self._build_done()

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return []

        # 如果有 event: 字段，使用它；否则从 JSON 中提取 type
        chunk_type = self._pending_event_type or chunk.get("type")
        self._pending_event_type = None

        events = []

        # message_start: 发送首个 chunk (role=assistant)
        if chunk_type == "message_start":
            if not self.started:
                self.started = True
                self._extract_usage(chunk)
                events.append(self._build_role_chunk())
            return events

        # content_block_start: 区分 text / tool_use
        if chunk_type == "content_block_start":
            cb = chunk.get("content_block", {})
            bt = cb.get("type", "text")

            if bt == "text":
                # 文本块开始，不需要发送额外内容
                pass
            elif bt == "tool_use":
                self.in_tool_call = True
                self._current_tool_call_id = cb.get("id", f"call_{uuid.uuid4().hex[:8]}")
                self._current_tool_name = cb.get("name", "")
                self._current_tool_args = ""
            return events

        # content_block_delta: 文本增量或工具参数增量
        if chunk_type == "content_block_delta":
            delta = chunk.get("delta", {})
            dt_type = delta.get("type", "")

            if dt_type == "text_delta":
                text = delta.get("text", "")
                self.output_tokens += len(text) // 4
                if text:
                    events.append(self._build_content_chunk(text))
            elif dt_type == "input_json_delta":
                # 工具参数增量
                partial = delta.get("partial_json", "")
                self._current_tool_args += partial
            elif dt_type == "thinking_delta":
                # thinking 增量（暂不处理）
                pass
            return events

        # content_block_stop: 工具调用结束
        if chunk_type == "content_block_stop":
            if self.in_tool_call and self._current_tool_call_id:
                events.append(self._build_tool_call_chunk())
                self.in_tool_call = False
                self._current_tool_call_id = None
                self._current_tool_name = None
                self._current_tool_args = ""
            return events

        # message_delta: 提取 stop_reason
        if chunk_type == "message_delta":
            delta_chunk = chunk.get("delta", {})
            stop_reason = delta_chunk.get("stop_reason", "stop")
            usage = chunk.get("usage", {})
            if usage.get("output_tokens"):
                self.output_tokens = usage["output_tokens"]
            if usage.get("input_tokens"):
                self.input_tokens = usage["input_tokens"]

            # 发送 finish
            finish_map = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
            finish = finish_map.get(stop_reason, "stop")
            events.append(self._build_finish_chunk(finish))
            return events

        # message_stop: 不需要处理
        if chunk_type == "message_stop":
            return []

        return events

    def _build_role_chunk(self) -> bytes:
        """构建首个 chunk: role=assistant"""
        import time
        self.created = int(time.time())

        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

    def _build_content_chunk(self, text: str) -> bytes:
        """构建 content chunk"""
        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

    def _build_tool_call_chunk(self) -> bytes:
        """构建 tool_calls chunk"""
        # 解析 arguments
        try:
            args_obj = json.loads(self._current_tool_args)
        except json.JSONDecodeError:
            args_obj = {}

        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {
                    "tool_calls": [{
                        "index": self.content_block_index,
                        "id": self._current_tool_call_id or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": self._current_tool_name or "unknown",
                            "arguments": self._current_tool_args
                        }
                    }]
                },
                "finish_reason": None
            }]
        }
        self.content_block_index += 1
        return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

    def _build_finish_chunk(self, finish_reason: str) -> bytes:
        """构建 finish chunk"""
        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason
            }]
        }
        return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")

    def _build_done(self) -> list[bytes]:
        """构建 [DONE] 信号"""
        return [b"data: [DONE]\n\n"]

    def _extract_usage(self, chunk: dict):
        """从 message_start 中提取 usage"""
        msg = chunk.get("message", {})
        usage = msg.get("usage", {})
        self.input_tokens = usage.get("input_tokens", 0)
        self.output_tokens = usage.get("output_tokens", 0)

    def get_usage(self) -> dict[str, int]:
        """获取 token 使用量"""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


async def convert_anthropic_sse_to_openai(
    async_iter,
    model: str
) -> AsyncGenerator[bytes, None]:
    """将 Anthropic SSE 流转换为 OpenAI SSE 流

    Args:
        async_iter: Anthropic SSE 的异步迭代器
        model: 模型名称
    """
    converter = AnthropicToOpenAISSEConverter(model)

    async for chunk in async_iter:
        if isinstance(chunk, bytes):
            raw = chunk
        elif isinstance(chunk, str):
            raw = chunk.encode("utf-8")
        else:
            continue

        events = converter.convert_chunk(raw)
        for event in events:
            yield event

    # 如果流结束了但还没有发送 [DONE]
    # （某些情况下流结束后才发送 [DONE]）