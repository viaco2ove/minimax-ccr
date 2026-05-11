"""
OpenAI SSE → Anthropic SSE 实时转换器。
"""

import json
import uuid
from typing import AsyncGenerator

import httpx


class OpenAISSEConverter:
    """OpenAI SSE → Anthropic SSE 实时转换器

    将 OpenAI Chat Completions 格式的 SSE 流转换为 Anthropic Messages 格式。
    """

    def __init__(self, model: str = ""):
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

        # 首个 chunk: 发送 message_start + content_block_start
        if not self.started:
            self.started = True
            events.append(self._build_message_start())
            events.append(self._build_content_block_start("text"))

        # role chunk: 跳过
        if "role" in delta and not delta.get("content") and not delta.get("tool_calls") and not delta.get("reasoning_content"):
            return events

        # reasoning_content → 简化处理（暂不转换为 thinking block）
        if delta.get("reasoning_content"):
            self.output_tokens += len(delta["reasoning_content"]) // 4
            # 暂不发送 reasoning 作为独立 block

        # content → text_delta
        if delta.get("content"):
            self.output_tokens += len(delta["content"]) // 4
            events.append(self._build_text_delta(delta["content"]))

        # tool_calls → tool_use blocks
        if delta.get("tool_calls"):
            for tc_delta in delta["tool_calls"]:
                tc_events = self._handle_tool_call_delta(tc_delta)
                events.extend(tc_events)

        # finish_reason → content_block_stop + message_delta
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
        return f"data: {json.dumps(msg_start)}\n\n".encode("utf-8")

    def _build_content_block_start(self, block_type: str, name: str = "", block_id: str = "") -> bytes:
        content_block = {"type": block_type, "text": ""} if block_type == "text" else {"type": block_type, "id": block_id, "name": name, "input": {}}
        event = {
            "type": "content_block_start",
            "index": self.content_block_index,
            "content_block": content_block
        }
        return f"data: {json.dumps(event)}\n\n".encode("utf-8")

    def _build_text_delta(self, text: str) -> bytes:
        delta = {
            "type": "content_block_delta",
            "index": self.content_block_index,
            "delta": {"type": "text_delta", "text": text}
        }
        return f"data: {json.dumps(delta)}\n\n".encode("utf-8")

    def _build_content_block_stop(self) -> bytes:
        event = {"type": "content_block_stop", "index": self.content_block_index}
        return f"data: {json.dumps(event)}\n\n".encode("utf-8")

    def _build_finish(self, finish_reason: str) -> list[bytes]:
        events = []

        # 关闭当前 content block
        if self.started:
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
        events.append(f"data: {json.dumps(msg_delta)}\n\n".encode("utf-8"))

        # message_stop
        msg_stop = {"type": "message_stop"}
        events.append(f"data: {json.dumps(msg_stop)}\n\n".encode("utf-8"))

        return events

    def _build_message_stop(self) -> list[bytes]:
        if self.started:
            return []  # 已经在 finish 中发送了

        # 如果没有收到过任何 chunk 就收到 [DONE]
        msg_start = self._build_message_start()
        content_start = self._build_content_block_start("text")
        text_delta = self._build_text_delta("")
        content_stop = self._build_content_block_stop()
        msg_delta = f'data: {json.dumps({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":0}})}\n\n'.encode("utf-8")
        msg_stop = f'data: {json.dumps({"type":"message_stop"})}\n\n'.encode("utf-8")
        return [msg_start, content_start, text_delta, content_stop, msg_delta, msg_stop]

    def _handle_tool_call_delta(self, tc_delta: dict) -> list[bytes]:
        """处理 tool_calls 的增量更新"""
        events = []

        # 新 tool call 开始
        if tc_delta.get("id"):
            # 关闭之前的 text block
            if not self.in_tool_call:
                if self.started:
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
            events.append(f"data: {json.dumps(delta)}\n\n".encode("utf-8"))

        return events


async def convert_openai_sse_stream(
    response: httpx.Response,
    model: str
) -> AsyncGenerator[bytes, None]:
    """将 OpenAI SSE 流转换为 Anthropic SSE 流"""
    converter = OpenAISSEConverter(model)

    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue

        # 转发非 data 行（如注释行）
        if not line.startswith("data: "):
            yield f"{line}\n".encode("utf-8")
            continue

        chunk = line.encode("utf-8")
        events = converter.convert_chunk(chunk)
        for event in events:
            yield event
