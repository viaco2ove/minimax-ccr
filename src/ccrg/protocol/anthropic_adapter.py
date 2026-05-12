"""
Anthropic 协议适配器 — 透传 + 微调。
"""

import json
import re
from typing import Any

from .base import ProtocolAdapter


class AnthropicAdapter(ProtocolAdapter):
    """Anthropic 协议适配器

    当 Provider 使用 Anthropic 协议时，直接透传请求和响应，
    只需要做一些微调（如合并 default_params、清理 system-reminder）。
    """

    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """对 Anthropic 格式请求做微调"""
        result = dict(request)

        # 1. 合并 default_params
        default_params = provider_config.get("default_params", {})
        for key, value in default_params.items():
            if key not in result:
                result[key] = value

        # 2. 确保 max_tokens 存在
        if "max_tokens" not in result:
            result["max_tokens"] = 4096

        # 3. 清理 system-reminder
        result = _strip_system_reminders(result)

        # 4. 如果 provider 不支持 thinking，剥离 thinking 字段
        capabilities = provider_config.get("capabilities", {})
        if not capabilities.get("thinking", False) and "thinking" in result:
            del result["thinking"]

        # 5. 如果 provider 不支持 vision，剥离 image 内容块
        if not capabilities.get("vision", False):
            result = _strip_images(result)

        # 6. 处理 output_config.effort 参数，确保值有效
        if "output_config" in result:
            output_config = result["output_config"]
            if isinstance(output_config, dict) and "effort" in output_config:
                effort = output_config["effort"]
                # 豆包等 API 只接受 low, medium, high, max
                valid_efforts = {"low", "medium", "high", "max"}
                if effort not in valid_efforts:
                    # 把 xhigh 映射到 high，其他无效值映射到 medium
                    if effort == "xhigh":
                        output_config["effort"] = "high"
                    else:
                        output_config["effort"] = "medium"

        return result

    def get_target_url(self, provider_config: dict, model: str | None = None) -> str:
        """获取 Anthropic 格式的 URL"""
        base = provider_config["api_base_url"].rstrip("/")
        if not base.endswith("/v1/messages"):
            base += "/v1/messages"
        return base

    def transform_json_response(self, response: dict, context: dict | None = None) -> dict:
        """Anthropic 响应直接透传"""
        return response

    def transform_response_headers(self, headers: dict) -> dict:
        """Anthropic 响应头直接透传"""
        return headers

    def needs_sse_event_prefix(self) -> bool:
        """Anthropic SSE 需要 event: 前缀"""
        return False

    def get_sse_event_name(self) -> str:
        return ""


def _strip_system_reminders(obj: Any) -> Any:
    """递归移除 system-reminder 块"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "system":
                result[k] = _strip_system_reminders(v)
            elif k != "cache_control":
                result[k] = _strip_system_reminders(v)
        return result
    elif isinstance(obj, list):
        return [_strip_system_reminders(item) for item in obj]
    elif isinstance(obj, str):
        # 移除 <system-reminder>...</system-reminder> 块
        return re.sub(r'<system-reminder>.*?</system-reminder>', '', obj, flags=re.DOTALL).strip()
    return obj


def extract_system(request: dict) -> str:
    """从 Anthropic 请求中提取 system prompt"""
    system = request.get("system", "")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for item in system:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return ""


def _strip_images(request: dict) -> dict:
    """从请求中移除 image 内容块，保留文本描述"""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return request

    changed = False
    new_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    changed = True
                    # 用文本占位替代，保留上下文连贯性
                    new_content.append({"type": "text", "text": "[image]"})
                else:
                    new_content.append(block)
            if changed:
                msg = dict(msg, content=new_content)
        new_messages.append(msg)

    if changed:
        request = dict(request, messages=new_messages)
    return request
