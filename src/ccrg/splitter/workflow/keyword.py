"""workflow 包内独立关键词策略（不复用下级 keyword.py）。"""

import logging

from ..base import RoutingDecision
from .common import extract_user_text, resolve_route_from_keywords, word_match

logger = logging.getLogger("ccrg")


class WorkflowKeywordStrategy:
    """基于关键词匹配的 workflow 阶段判定策略（行为对齐下级 KeywordSplitter）"""

    def __init__(self, config: dict | None, keywords: dict, registry=None, usage_stats=None):
        self.config = config or {}
        self.keywords = keywords or {}

    def detect(self, body: dict) -> RoutingDecision:
        workflow_keywords = self.keywords.get("workflow_intent", {})
        chat_keywords = workflow_keywords.get("chat_intention", []) or []
        task_keywords = workflow_keywords.get("intention_analyze", []) or []

        user_text = extract_user_text(body).lower()

        chat_score = sum(1 for kw in chat_keywords if word_match(kw, user_text))
        task_score = sum(1 for kw in task_keywords if word_match(kw, user_text))

        logger.debug(f"[WorkflowKeywordStrategy] chat={chat_score}, task={task_score}")

        if task_score > chat_score:
            matched = [kw for kw in task_keywords if word_match(kw, user_text)]
            route, fallback = resolve_route_from_keywords(self.config, self.keywords, "task")
            logger.info(f"[WorkflowKeywordStrategy] matched task keywords: {matched}")
            return RoutingDecision(
                intent="task",
                route=route,
                matched_rule="workflow_keyword_routing",
                matched_reason=f"keywords={matched}",
                fallback=fallback,
                workflow_stage="intention_analyze",
            )

        if chat_score > 0:
            matched = [kw for kw in chat_keywords if word_match(kw, user_text)]
            route, fallback = resolve_route_from_keywords(self.config, self.keywords, "chat")
            logger.info(f"[WorkflowKeywordStrategy] matched chat keywords: {matched}")
            return RoutingDecision(
                intent="chat",
                route=route,
                matched_rule="workflow_keyword_routing",
                matched_reason=f"keywords={matched}",
                fallback=fallback,
                workflow_stage="chat_intention",
            )

        # 未命中，返回 default
        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return RoutingDecision(
            intent="chat",
            route=default,
            matched_rule="workflow_keyword_routing",
            matched_reason="no_keywords_matched",
            fallback=None,
            workflow_stage=None,
        )
