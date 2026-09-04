"""
KeywordSplitter — 基于关键词的工作流意图分流器。
"""

import logging
import re
from typing import Any, Literal

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class KeywordSplitter(Splitter):
    """基于关键词检测工作流意图 — 取代 keyword_routing"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        super().__init__(usage_stats=usage_stats)
        self.splitter_type = "keyword"
        self.keywords = keywords
        self.config = config or {}
        # usage_stats 不用于 keyword_splitter

    def detect(self, body: dict) -> RoutingDecision:
        """检测用户意图并返回完整路由决策"""
        import time as _time
        start = _time.time()
        workflow_keywords = self.keywords.get("workflow_intent", {})
        chat_keywords = workflow_keywords.get("chat_intention", [])
        task_keywords = workflow_keywords.get("intention_analyze", [])

        # 从 routing.keyword_routing.rules 取路由配置
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        user_text = self._extract_user_text(body).lower()

        chat_score = sum(1 for kw in chat_keywords if self._word_match(kw, user_text))
        task_score = sum(1 for kw in task_keywords if self._word_match(kw, user_text))

        logger.debug(f"KeywordSplitter: chat={chat_score}, task={task_score}")

        if task_score > chat_score:
            if task_score > 0:
                matched = [kw for kw in task_keywords if self._word_match(kw, user_text)]
                logger.info(f"KeywordSplitter matched task keywords: {matched}")
            route, fallback = self._resolve_route("task", rules)
            decision = RoutingDecision(
                intent="task",
                route=route,
                matched_rule="keyword_routing",
                matched_reason=f"keywords={matched}",
                fallback=fallback,
                workflow_stage="intention_analyze",
            )
            self._record(decision, (_time.time() - start) * 1000)
            return decision

        if chat_score > 0:
            matched = [kw for kw in chat_keywords if self._word_match(kw, user_text)]
            logger.info(f"KeywordSplitter matched chat keywords: {matched}")
            route, fallback = self._resolve_route("chat", rules)
            decision = RoutingDecision(
                intent="chat",
                route=route,
                matched_rule="keyword_routing",
                matched_reason=f"keywords={matched}",
                fallback=fallback,
                workflow_stage="chat_intention",
            )
            self._record(decision, (_time.time() - start) * 1000)
            return decision

        # 未命中，返回 default
        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        decision = RoutingDecision(
            intent="chat",
            route=default,
            matched_rule="keyword_routing",
            matched_reason="no_keywords_matched",
            fallback=None,
            workflow_stage=None,
        )
        self._record(decision, (_time.time() - start) * 1000)
        return decision

    def _resolve_route(self, intent: str, rules: list[dict]) -> tuple[str, list[str] | None]:
        """从 keyword_routing.rules 中找到 intent 对应的路由"""
        # keywords.json 中 chat 对应 chat_intention，task 对应 intention_analyze
        kw_map = {"chat": "chat_intention", "task": "intention_analyze"}
        target_kw_group = kw_map.get(intent, "")

        for rule in rules:
            rule_kws = rule.get("keywords", [])
            wflow = self.keywords.get("workflow_intent", {})
            group_kws = wflow.get(target_kw_group, [])
            if any(kw in rule_kws for kw in group_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None

    def _word_match(self, kw: str, text: str) -> bool:
        """单词边界匹配，避免子串误匹配（如 'hi' 命中 'think'）"""
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        return bool(re.search(pattern, text))

    def _extract_user_text(self, body: dict) -> str:
        """提取用户消息文本，剥离 <system-reminder> 块以减少会话压缩摘要的干扰"""
        texts = []

        for msg in body.get("messages", []):
            role = msg.get("role", "")
            if role != "user":
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                joined = content
            elif isinstance(content, list):
                joined = " ".join(
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            else:
                joined = ""

            # 剥离 system-reminder 块，减少压缩摘要的噪声干扰
            joined = re.sub(r"<system-reminder[^>]*>.*?</system-reminder>", " ", joined, flags=re.DOTALL)
            texts.append(joined)

        return " ".join(texts)
