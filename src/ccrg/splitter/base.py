"""
Splitter 模块基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


# workflow 阶段分类映射表：splitter category → workflow_stage
# 供 semantic_local / llm / keyword 各 splitter 复用
WORKFLOW_STAGE_CATEGORY_MAP: dict[str, str] = {
    "chat_intention": "chat_intention",
    "intention_analyze": "intention_analyze",
    "problem_analyze": "analyze_plan",
    "analyze_plan": "analyze_plan",
    "solution_plan": "execute_solve",
    "execute_solve": "execute_solve",
}


def resolve_workflow_stage(category_scores: dict[str, float], threshold: float = 0.0) -> str | None:
    """按最高分 category 映射 workflow_stage。

    Args:
        category_scores: {category: 最高分}，如 semantic 的 best_scores / llm 的每类最高分
        threshold: 最低命中分数（低于该值视为未命中，返回 None）

    Returns:
        workflow_stage 或 None（无命中 / 分数低于阈值）
    """
    if not category_scores:
        return None
    best_cat = max(category_scores, key=lambda c: category_scores.get(c, 0.0))
    if category_scores.get(best_cat, 0.0) < threshold:
        return None
    return WORKFLOW_STAGE_CATEGORY_MAP.get(best_cat)


@dataclass
class RoutingDecision:
    """分流器返回的路由决策"""
    intent: Literal["chat", "task"]
    route: str  # provider:model
    matched_rule: str = ""   # 匹配规则名
    matched_reason: str = ""  # 匹配原因
    fallback: list[str] | None = None  # fallback 链
    workflow_stage: str | None = None  # workflow 阶段（独立 workflow splitter 使用）


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