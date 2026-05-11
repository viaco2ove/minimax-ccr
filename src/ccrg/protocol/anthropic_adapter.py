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
