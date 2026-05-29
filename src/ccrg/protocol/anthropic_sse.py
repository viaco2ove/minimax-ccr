"""
Anthropic SSE 格式规范化器。

用于确保 Anthropic 格式的 SSE 响应包含正确的 event: 字段，
并且事件序列完整（message_start → content_block_start → content_block_delta → content_block_stop → message_delta → message_stop）。
"""

import json
import uuid
import logging
from typing import Optional

logger = logging.getLogger("ccrg")


class AnthropicSSEConverter:
    """Anthropic SSE 格式规范化器

    接收 Anthropic 格式的 SSE 流，确保：
    1. 每个 data: 行都有正确的 event: 前缀
    2. 事件序列完整且顺序正确
    3. 首个 chunk 包含 message_start
    4. 结束时有完整的 message_delta + message_stop
    """

    def __init__(self, model: str = ""):
        self.model = model
        self.msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.content_block_index = 0
        self.started = False
        self.output_tokens = 0
        self.input_tokens = 0
        self.stop_reason = None
        self.has_sent_message_start = False
        self.has_sent_content_block_start = False
        self.has_sent_content_block_stop = False
        self.has_sent_message_delta = False
        self.has_sent_message_stop = False
        self._pending_event_type: Optional[str] = None  # 缓存 event: 字段

    def convert_chunk(self, raw_chunk: bytes) -> list[bytes]:
        """将一个 Anthropic SSE chunk 转换为标准化的 Anthropic SSE events

        返回列表因为一个 chunk 可能产生多个 events（如 message_start + content_block_start）
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
            return self._build_message_stop_sequence()

        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse Anthropic SSE chunk: {data[:100]}")
            return []

        # 如果有 event: 字段，使用它；否则从 JSON 中提取 type
        chunk_type = self._pending_event_type or chunk.get("type")
        self._pending_event_type = None  # 清除缓存
        events = []

        # 首个 chunk: 确保发送 message_start
        if not self.has_sent_message_start:
            self.has_sent_message_start = True
            events.append(self._build_message_start(chunk))

        # 根据 chunk type 处理
        if chunk_type == "message_start":
            # 提取 usage 信息
            msg = chunk.get("message", {})
            self.msg_id = msg.get("id", self.msg_id)
            self.model = msg.get("model", self.model)
            usage = msg.get("usage", {})
            self.input_tokens = usage.get("input_tokens", 0)
            # 不需要转发，已在上面构建
            return events

        elif chunk_type == "content_block_start":
            if not self.has_sent_content_block_start:
                self.has_sent_content_block_start = True
                self.content_block_index = chunk.get("index", 0)
            # 转发 content_block_start
            events.append(self._format_sse_event(chunk))
            return events

        elif chunk_type == "content_block_delta":
            # 确保 content_block_start 已发送
            if not self.has_sent_content_block_start:
                self.has_sent_content_block_start = True
                self.content_block_index = chunk.get("index", 0)
                events.append(self._build_content_block_start(chunk.get("index", 0)))

            # 提取文本增量
            delta = chunk.get("delta", {})
            if delta.get("type") == "text_delta":
                self.output_tokens += len(delta.get("text", "")) // 4
            elif delta.get("type") == "thinking_delta":
                self.output_tokens += len(delta.get("thinking", "")) // 4
            elif delta.get("type") == "input_json_delta":
                pass  # tool call 参数

            # 转发 content_block_delta
            events.append(self._format_sse_event(chunk))
            return events

        elif chunk_type == "content_block_stop":
            self.has_sent_content_block_stop = True
            # 转发 content_block_stop
            events.append(self._format_sse_event(chunk))
            return events

        elif chunk_type == "message_delta":
            self.stop_reason = chunk.get("delta", {}).get("stop_reason")
            usage = chunk.get("usage", {})
            if usage.get("output_tokens"):
                self.output_tokens = usage["output_tokens"]

            self.has_sent_message_delta = True
            # 转发 message_delta
            events.append(self._format_sse_event(chunk))
            return events

        elif chunk_type == "message_stop":
            self.has_sent_message_stop = True
            # 不需要转发，已在 _build_message_stop_sequence 中处理
            return []

        else:
            # 未知类型，直接转发
            events.append(self._format_sse_event(chunk))
            return events

    def _build_message_start(self, chunk: dict) -> bytes:
        """构建 message_start 事件"""
        msg = chunk.get("message", {})
        usage = msg.get("usage", {"input_tokens": 0, "output_tokens": 0})

        msg_start = {
            "type": "message_start",
            "message": {
                "id": self.msg_id,
                "type": "message",
                "role": "assistant",
                "model": self.model or msg.get("model", ""),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": usage
            }
        }
        return self._format_sse_event(msg_start)

    def _build_content_block_start(self, index: int) -> bytes:
        """构建 content_block_start 事件"""
        event = {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "text",
                "text": ""
            }
        }
        return self._format_sse_event(event)

    def _build_message_stop_sequence(self) -> list[bytes]:
        """构建完整的消息结束序列"""
        events = []

        # 如果连 message_start 都没发送过（空响应），发送完整的空消息序列
        if not self.has_sent_message_start:
            self.has_sent_message_start = True
            self.has_sent_content_block_start = True
            self.has_sent_content_block_stop = True
            self.has_sent_message_delta = True
            self.has_sent_message_stop = True
            
            # 发送完整序列：message_start → content_block_start → content_block_delta → content_block_stop → message_delta → message_stop
            events.append(self._format_sse_event({
                "type": "message_start",
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": self.input_tokens,
                        "output_tokens": 0
                    }
                }
            }))
            events.append(self._format_sse_event({
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "text",
                    "text": ""
                }
            }))
            events.append(self._format_sse_event({
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "text_delta",
                    "text": ""
                }
            }))
            events.append(self._format_sse_event({
                "type": "content_block_stop",
                "index": 0
            }))
            events.append(self._format_sse_event({
                "type": "message_delta",
                "delta": {
                    "stop_reason": "end_turn",
                    "stop_sequence": None
                },
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": 0
                }
            }))
            events.append(self._format_sse_event({
                "type": "message_stop"
            }))
            return events

        # 如果还没有发送 content_block_stop，发送一个
        if self.has_sent_content_block_start and not self.has_sent_content_block_stop:
            event = {
                "type": "content_block_stop",
                "index": self.content_block_index
            }
            events.append(self._format_sse_event(event))
            self.has_sent_content_block_stop = True

        # 如果还没有发送 message_delta，发送一个
        if not self.has_sent_message_delta:
            msg_delta = {
                "type": "message_delta",
                "delta": {
                    "stop_reason": self.stop_reason or "end_turn",
                    "stop_sequence": None
                },
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens
                }
            }
            events.append(self._format_sse_event(msg_delta))
            self.has_sent_message_delta = True

        # 发送 message_stop
        if not self.has_sent_message_stop:
            msg_stop = {"type": "message_stop"}
            events.append(self._format_sse_event(msg_stop))
            self.has_sent_message_stop = True

        return events

    def _format_sse_event(self, event: dict) -> bytes:
        """将事件格式化为 SSE 格式"""
        event_type = event.get("type", "message_delta")
        data = json.dumps(event, ensure_ascii=False)
        return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")

    def get_usage(self) -> dict[str, int]:
        """获取 token 使用量

        Returns:
            {"input_tokens": int, "output_tokens": int}
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
