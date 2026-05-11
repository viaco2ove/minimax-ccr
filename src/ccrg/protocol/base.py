"""
Protocol adapter 基类。
"""

from abc import ABC, abstractmethod
from typing import Any


class ProtocolAdapter(ABC):
    """协议适配器基类"""

    @abstractmethod
    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """将 Anthropic 格式请求转换为目标 Provider 的格式"""
        ...

    @abstractmethod
    def get_target_url(self, provider_config: dict, model: str | None = None) -> str:
        """获取目标 Provider 的完整 URL"""
        ...

    def transform_response_headers(self, headers: dict) -> dict:
        """转换响应头（可选实现）"""
        return headers

    @abstractmethod
    def transform_json_response(self, response: dict, context: dict | None = None) -> dict:
        """转换非流式 JSON 响应"""
        ...

    def get_sse_event_name(self) -> str:
        """返回 SSE event 名称（Anthropic 格式需要）"""
        return ""

    def needs_sse_event_prefix(self) -> bool:
        """是否需要为 SSE chunk 添加 event 前缀"""
        return False
