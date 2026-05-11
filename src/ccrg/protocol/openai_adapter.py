"""
OpenAI 协议适配器 — anthropic ↔ openai 双向转换。
"""

import json
import uuid
from typing import Any

from .base import ProtocolAdapter
from .anthropic_adapter import extract_system, _strip_system_reminders


class OpenAIAdapter(ProtocolAdapter):
    """OpenAI 协议适配器

    将 Anthropic Messages 格式转换为 OpenAI Chat Completions 格式，
    以及将 OpenAI 响应转换回 Anthropic 格式。
    """

    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """将 Anthropic 请求转换为 OpenAI 格式"""
        result = {}

        # model
        model = request.get("model", provider_config["models"][0] if provider_config["models"] else "")
        result["model"] = model

        # messages
        result["messages"] = self._convert_messages(request)

        # stream
        result["stream"] = request.get("stream", False)

        # tools
        tools = self._convert_tools(request.get("tools", []))
        if tools:
            result["tools"] = tools

        # tool_choice
        if "tool_choice" in request:
            result["tool_choice"] = self._convert_tool_choice(request["tool_choice"])

        # 参数映射
        if "max_tokens" in request:
            result["max_tokens"] = request["max_tokens"]
        if "temperature" in request:
            result["temperature"] = request["temperature"]
        if "stop_sequences" in request:
            result["stop"] = request["stop_sequences"]
        if "top_p" in request:
            result["top_p"] = request["top_p"]

        # thinking → reasoning_effort
        thinking = request.get("thinking")
        if thinking:
            result["reasoning_effort"] = "high"

        # 合并 default_params
        default_params = provider_config.get("default_params", {})
        for key, value in default_params.items():
            if key not in result:
                result[key] = value

        return result

    def get_target_url(self, provider_config: dict, model: str | None = None) -> str:
        """获取 OpenAI 格式的 URL"""
        base = provider_config["api_base_url"].rstrip("/")
        if not base.endswith("/v1/chat/completions"):
            base += "/v1/chat/completions"
        return base

    def _convert_messages(self, request: dict) -> list[dict]:
        """转换 messages 数组"""
        messages = []
        system = extract_system(request)

        if system:
            messages.append({"role": "system", "content": system})

        for msg in request.get("messages", []):
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user":
                converted = self._convert_user_message(msg)
                if isinstance(converted, list):
                    messages.extend(converted)
                else:
                    messages.append(converted)
            elif role == "assistant":
                messages.append(self._convert_assistant_message(msg))
            elif role == "tool":
                # tool 结果直接追加
                tool_id = msg.get("tool_call_id", "")
                tool_content = content
                if isinstance(tool_content, list):
                    tool_content = " ".join(
                        b.get("text", "") for b in tool_content if isinstance(b, dict)
                    )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": str(tool_content) if tool_content else "[empty]"
                })

        return messages

    def _convert_user_message(self, msg: dict) -> list[dict] | dict:
        """转换 user message"""
        content = msg.get("content", "")

        if isinstance(content, str):
            return {"role": "user", "content": content}

        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type", "")

                if block_type == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})

                elif block_type == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"}
                        })
                    elif source.get("type") == "url":
                        parts.append({
                            "type": "image_url",
                            "image_url": {"url": source.get("url", "")}
                        })

                elif block_type == "tool_result":
                    # tool_result 需要拆成独立的 tool message
                    tool_id = block.get("tool_use_id", "")
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = " ".join(
                            b.get("text", "") for b in result_content if isinstance(b, dict)
                        )
                    return [{
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": str(result_content)
                    }]

            return {"role": "user", "content": parts} if parts else {"role": "user", "content": ""}

        return {"role": "user", "content": str(content) if content else ""}

    def _convert_assistant_message(self, msg: dict) -> dict:
        """转换 assistant message"""
        content = msg.get("content", "")
        result = {"role": "assistant"}

        if isinstance(content, str):
            result["content"] = content
            return result

        if isinstance(content, list):
            text_parts = []
            tool_calls = []

            for block in content:
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type", "")

                if block_type == "text":
                    text_parts.append(block.get("text", ""))

                elif block_type == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)
                        }
                    })

                # thinking 块暂不转换

            result["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                result["tool_calls"] = tool_calls

        return result

    def _convert_tools(self, tools: list) -> list[dict]:
        """转换 tool 定义"""
        if not tools:
            return []

        oai_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue

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

    def _convert_tool_choice(self, tool_choice: dict) -> dict:
        """转换 tool_choice"""
        if not isinstance(tool_choice, dict):
            return tool_choice

        choice_type = tool_choice.get("type", "")

        if choice_type == "auto":
            return {"type": "auto"}
        elif choice_type == "any":
            return {"type": "required"}
        elif choice_type == "tool":
            name = tool_choice.get("name", "")
            return {"type": "function", "function": {"name": name}}

        return tool_choice

    def transform_json_response(self, response: dict, context: dict | None = None) -> dict:
        """将 OpenAI Chat Completions 响应转换为 Anthropic Messages 响应"""
        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        content_blocks = []

        # reasoning_content → 暂不转换为 thinking block（简化处理）
        # text content
        text = message.get("content", "")
        if text:
            content_blocks.append({
                "type": "text",
                "text": text
            })

        # tool_calls → tool_use blocks
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

        # stop_reason 映射
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = stop_reason_map.get(finish_reason, "end_turn")

        # usage 映射
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

    def needs_sse_event_prefix(self) -> bool:
        """OpenAI SSE 没有 event: 前缀"""
        return False
