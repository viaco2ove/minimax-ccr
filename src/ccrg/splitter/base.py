"""
Splitter 模块基类。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


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

    # 子类可设置：当前 splitter 类型名（用于 usage_stats 记录 model 字段）
    splitter_type: str = "unknown"

    def __init__(self, usage_stats: Any = None):
        # usage_stats 由 SplitterFactory 注入；splitter 自身不直接计 token，
        # 但会记录"被调用一次"以便观察 llm_splitter / semantic 等是否实际被使用。
        self.usage_stats = usage_stats

    @abstractmethod
    def detect(self, body: dict) -> RoutingDecision:
        """根据请求内容决定路由

        Args:
            body: 请求体（OpenAI 或 Anthropic 格式）

        Returns:
            RoutingDecision: 包含意图、路由目标、匹配规则
        """
        ...

    def _record(self, decision: RoutingDecision, latency_ms: float, success: bool = True) -> None:
        """记录 splitter 调用到 usage_stats

        Args:
            decision: 路由决策（用于提取 matched_rule / matched_keyword）
            latency_ms: splitter 处理耗时
            success: 是否成功生成决策（fallback 也算成功）
        """
        if not self.usage_stats:
            return

        matched_keyword = ""
        if decision.matched_reason.startswith("keywords="):
            matched_keyword = decision.matched_reason[len("keywords="):]
        else:
            matched_keyword = decision.matched_reason or ""

        try:
            self.usage_stats.record(
                provider="splitter",
                model=self.splitter_type,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                success=1 if success else 0,
                route_rule=decision.matched_rule or self.splitter_type,
                matched_keyword=matched_keyword,
                matched_rule=decision.matched_rule or "",
            )
        except Exception:
            pass