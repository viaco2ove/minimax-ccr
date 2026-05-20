"""
Splitter 模块基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class RoutingDecision:
    """分流器返回的路由决策"""
    intent: Literal["chat", "task"]
    route: str  # provider:model
    matched_rule: str = ""   # 匹配规则名
    matched_reason: str = ""  # 匹配原因
    fallback: list[str] | None = None  # fallback 链


class Splitter(ABC):
    """分流器抽象基类 — 取代 keyword_routing"""

    @abstractmethod
    def detect(self, body: dict) -> RoutingDecision:
        """根据请求内容决定路由

        Args:
            body: 请求体（OpenAI 或 Anthropic 格式）

        Returns:
            RoutingDecision: 包含意图、路由目标、匹配规则
        """
        ...