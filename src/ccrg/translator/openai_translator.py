"""Anthropic SSE -> OpenAI SSE 实时转换器。"""

import json
import time
import uuid
import logging
from typing import AsyncGenerator, Optional

from starlette.responses import StreamingResponse as StarletteStreamingResponse

logger = logging.getLogger(__name__)


def convert_streaming_to_openai(response, model: str = ""):
    """将 Anthropic SSE 流式响应转换为 OpenAI Chat Completions SSE 格式

    Args:
        response: 上游返回的流式响应对象（包含 body_iterator）
        model: 模型名称

    Returns:
        StreamingResponse: OpenAI SSE 格式的流式响应
    """
    async def convert_stream():
        converter = AnthropicToOpenAISSEConverter(model)

        try:
            async for chunk in response.body_iterator:
                # 将 Anthropic SSE chunk 转换为 OpenAI SSE chunk
                events = converter.convert_chunk(chunk)
                for event in events:
                    yield event

            # 发送 [DONE]
            yield b"data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"Stream conversion error: {e}")
            # 发送错误
            error_chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": f"Error: {str(e)}"}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

    return StarletteStreamingResponse(
        convert_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"}
    )


async def collect_and_convert_to_json(response, model: str = "") -> dict:
    """收集流式响应的所有 chunks，然后转换为非流式 JSON

    Args:
        response: 上游返回的流式响应对象（包含 body_iterator）
        model: 模型名称

    Returns:
        dict: OpenAI Chat Completions JSON 格式的响应
    """
    converter = AnthropicToOpenAISSEConverter(model)

    # 收集所有 chunks
    all_chunks = []

    try:
        # 异步迭代 body_iterator
        async for chunk in response.body_iterator:
            all_chunks.append(chunk)
            logger.debug(f"[TRANSLATOR_OPENAI] chunk collected: {len(chunk)} bytes")
    except Exception as e:
        logger.error(f"[TRANSLATOR_OPENAI] Error collecting chunks: {e}")

    logger.debug(f"[TRANSLATOR_OPENAI] Collected {len(all_chunks)} chunks")

    # 处理每个 chunk
    for chunk in all_chunks:
        converter.convert_chunk(chunk)

    # 构建响应
    usage = converter.get_usage()
    stop_reason = converter.stop_reason or "stop"
    finish_map = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
    finish = finish_map.get(stop_reason, "stop")

    full_text = converter.get_full_text()
    tool_calls = converter.get_tool_calls()

    message = {"role": "assistant", "content": full_text or None}
    if tool_calls:
        message["tool_calls"] = [
            {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
            for tc in tool_calls
        ]

    resp_data = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        }
    }
    logger.debug(f"[TRANSLATOR_OPENAI] [RespData]: {resp_data}")
    return resp_data


class AnthropicToOpenAISSEConverter:
    """Anthropic SSE -> OpenAI SSE 实时转换器"""

    def __init__(self, model: str = ""):
        self.model = model
        self.chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self.created: int = 0
        self.content_block_index = 0
        self.started = False
        self.output_tokens = 0
        self.input_tokens = 0
        self.stop_reason: Optional[str] = None
        self._in_thinking_block = False
        self._in_tool_call = False
        self._current_tool_call_index = 0
        self._current_block_type: Optional[str] = None
        self._current_tool_call_id: Optional[str] = None
        self._current_tool_name: Optional[str] = None
        self._current_tool_args: str = ""
        self._pending_thinking_deltas: list = []
        self._pending_event_type: Optional[str] = None
        # 用于非流式响应收集
        self._full_text: str = ""
        self._tool_calls: list = []

    def convert_chunk(self, raw_chunk: bytes) -> list:
        line = raw_chunk.decode("utf-8", errors="replace").strip()
        logger.debug(f"[OpenaiTranslator] [RawChunk] [Line]: {line}")
        if not line:
            return []
        if line.startswith("event: "):
            self._pending_event_type = line[7:].strip()
            logger.debug(f"[CONVERTER] event: {self._pending_event_type}")
            return []
        if not line.startswith("data: "):
            logger.debug(f"[CONVERTER] non-data line: {line[:100]}")
            return []
        data = line[6:]
        if data.strip() == "[DONE]":
            logger.debug(f"[CONVERTER] received [DONE]")
            return self._handle_done()
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"[CONVERTER] JSON decode error: {data[:100]}")
            return []
        chunk_type = self._pending_event_type or chunk.get("type")
        self._pending_event_type = None
        logger.debug(f"[CONVERTER] Processing chunk type={chunk_type}, data={json.dumps(chunk, ensure_ascii=False)[:300]}")
        return self._process_chunk(chunk_type, chunk)

    def _process_chunk(self, chunk_type: str, chunk: dict) -> list:
        events = []
        logger.debug(f"[CONVERTER] _process_chunk type={chunk_type}")
        if chunk_type == "message_start":
            events.extend(self._handle_message_start(chunk))
        elif chunk_type == "content_block_start":
            events.extend(self._handle_content_block_start(chunk))
        elif chunk_type == "content_block_delta":
            events.extend(self._handle_content_block_delta(chunk))
        elif chunk_type == "content_block_stop":
            events.extend(self._handle_content_block_stop(chunk))
        elif chunk_type == "message_delta":
            events.extend(self._handle_message_delta(chunk))
        else:
            logger.warning(f"[CONVERTER] Unknown chunk type: {chunk_type}")
        logger.debug(f"[CONVERTER] Produced {len(events)} events")
        return events

    def _handle_message_start(self, chunk: dict) -> list:
        events = []
        if not self.started:
            self.started = True
            self.created = int(time.time())
            msg = chunk.get("message", {})
            usage = msg.get("usage", {})
            self.input_tokens = usage.get("input_tokens", 0)
            self.output_tokens = usage.get("output_tokens", 0)
            events.append(self._build_chunk({"role": "assistant"}))
        return events

    def _handle_content_block_start(self, chunk: dict) -> list:
        events = []
        content_block = chunk.get("content_block", {})
        block_type = content_block.get("type", "text")
        self._current_block_type = block_type
        if block_type == "thinking":
            self._in_thinking_block = True
            self._pending_thinking_deltas = []
        elif block_type == "tool_use":
            self._in_tool_call = True
            self._current_tool_call_id = content_block.get("id", f"call_{uuid.uuid4().hex[:8]}")
            self._current_tool_name = content_block.get("name", "")
            self._current_tool_args = ""
            self._current_tool_call_index = chunk.get("index", 0)
        return events

    def _handle_content_block_delta(self, chunk: dict) -> list:
        events = []
        delta = chunk.get("delta", {})
        delta_type = delta.get("type", "")
        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                self.output_tokens += len(text) // 4
                self._full_text += text
                events.append(self._build_chunk({"content": text}))
        elif delta_type == "thinking_delta":
            thinking_text = delta.get("thinking", "")
            if thinking_text:
                self._pending_thinking_deltas.append(thinking_text)
                self._full_text += thinking_text  # 同时累加到 full_text
                self.output_tokens += len(thinking_text) // 4
        elif delta_type == "input_json_delta":
            self._current_tool_args += delta.get("partial_json", "")
        return events

    def _handle_content_block_stop(self, chunk: dict) -> list:
        events = []
        block_type = self._current_block_type
        if block_type == "tool_use" and self._in_tool_call:
            if self._current_tool_call_id:
                events.append(self._build_tool_call_chunk())
                # 收集 tool_call 用于非流式响应
                self._tool_calls.append({
                    "id": self._current_tool_call_id,
                    "name": self._current_tool_name or "unknown",
                    "arguments": self._current_tool_args
                })
            self._in_tool_call = False
            self._current_tool_call_id = None
            self._current_tool_name = None
            self._current_tool_args = ""
        if block_type == "thinking":
            self._in_thinking_block = False
            self._pending_thinking_deltas = []
        self._current_block_type = None
        return events

    def _handle_message_delta(self, chunk: dict) -> list:
        events = []
        delta = chunk.get("delta", {})
        self.stop_reason = delta.get("stop_reason", "stop")
        usage = chunk.get("usage", {})
        if usage.get("output_tokens"):
            self.output_tokens = usage["output_tokens"]
        finish_map = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}
        finish = finish_map.get(self.stop_reason, "stop")
        events.append(self._build_chunk({}, finish))
        return events

    def _handle_done(self) -> list:
        events = []
        if self.started and not self.stop_reason:
            events.append(self._build_chunk({}, "stop"))
        events.append(b"data: [DONE]\n\n")
        return events

    def _build_chunk(self, delta: dict, finish_reason: Optional[str] = None) -> bytes:
        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    def _build_tool_call_chunk(self) -> bytes:
        chunk = {
            "id": self.chat_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{
                "index": self._current_tool_call_index,
                "delta": {
                    "tool_calls": [{
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
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    def get_usage(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    def get_full_text(self) -> str:
        """获取累积的完整文本"""
        return self._full_text

    def get_tool_calls(self) -> list:
        """获取收集的工具调用"""
        return self._tool_calls
