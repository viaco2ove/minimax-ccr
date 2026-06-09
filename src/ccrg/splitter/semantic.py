"""
SemanticSplitter — 基于语义向量的工作流意图分流器（API 版）。

使用外部 embedding API 获取向量，计算每个关键词与用户输入的相似度，返回命中的关键词列表。
"""

import json
import logging
import math
from typing import Any

import httpx

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class SemanticSplitter(Splitter):
    """基于语义向量相似度检测关键词并返回路由 — 取代 keyword_routing"""

    DEFAULT_THRESHOLD = 0.6

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        self.config = config or {}
        self.keywords = keywords
        self.registry = registry
        # usage_stats 不用于 semantic

        splitter_cfg = self.config.get("routing", {}).get("splitter", {})
        sem_cfg = splitter_cfg.get("semantic_splitter", {})
        self.embedding_api = sem_cfg.get("embedding_api", "")
        self.embedding_api_key = sem_cfg.get("embedding_api_key", "")
        self.threshold = splitter_cfg.get("threshold", self.DEFAULT_THRESHOLD)

        # 从 keywords.json 预取关键词
        wflow = self.keywords.get("workflow_intent", {})
        self._chat_keywords: list[str] = wflow.get("chat_intention", [])
        self._task_keywords: list[str] = wflow.get("intention_analyze", [])

        if self.embedding_api:
            logger.info(f"[SemanticSplitter] configured: api={self.embedding_api}, threshold={self.threshold}")
        else:
            logger.warning("SemanticSplitter: no embedding_api configured, will use keyword fallback")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配关键词并返回路由决策"""
        user_text = self._extract_user_text(body)
        if not user_text.strip():
            return self._keyword_fallback(body)

        user_emb = self._get_embedding(user_text)
        if user_emb is None:
            return self._keyword_fallback(body)

        # 遍历所有关键词，计算相似度，找出命中的关键词
        matched = self._match_keywords(user_emb)

        logger.info(f"[SemanticSplitter] matched: {matched}")

        # 根据命中关键词解析路由
        route_str, fb, intent = self._resolve_route_from_keywords(matched)
        return RoutingDecision(
            intent=intent,
            route=route_str,
            matched_rule="semantic_routing",
            matched_reason=f"keywords={matched}" if matched else "no_match",
            fallback=fb,
        )

    def _match_keywords(self, user_emb: list[float]) -> dict:
        """计算用户输入与每个关键词的相似度，返回每个 category 相似度最高的关键词"""
        result = {}

        wflow = self.keywords.get("workflow_intent", {})
        categories = ["chat_intention", "intention_analyze", "problem_analyze", "solution_plan", "execute_solve"]

        for category in categories:
            kw_list = wflow.get(category, [])
            # 如果是 problem_analyze 并且为空，就用 intention_analyze 顶替
            if category == "problem_analyze" and not kw_list:
                kw_list = wflow.get("analyze_plan", [])

            # 如果还是空，就跳过当前分类
            if not kw_list:
                continue

            # 计算所有关键词的相似度
            scores = []
            for kw in kw_list:
                kw_emb = self._get_embedding(kw)
                if kw_emb is None:
                    continue
                score = self._cosine(user_emb, kw_emb)
                scores.append((kw, score))

            # 按相似度降序排序，取最高分
            scores.sort(key=lambda x: x[1], reverse=True)

            # 只取相似度最高且超过阈值的关键词（最多 3 个）
            top_kws = [kw for kw, score in scores[:3] if score >= self.threshold]

            if top_kws:
                result[category] = top_kws

        return result

    def _resolve_route_from_keywords(self, matched: dict) -> tuple[str, list[str] | None, str]:
        """根据命中关键词从 keyword_routing.rules 找路由，和 llm_splitter 逻辑一致"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        # 合并所有命中的关键词
        all_matched_kws = []
        for kws in matched.values():
            all_matched_kws.extend(kws)

        # 判断意图：task_keywords 命中多则 task，否则 chat
        task_count = len(matched.get("intention_analyze", [])) + len(matched.get("problem_analyze", []))
        chat_count = len(matched.get("chat_intention", []))
        intent = "task" if task_count > chat_count else "chat"

        # 用所有命中关键词去 rules 里匹配
        for rule in rules:
            rule_kws = rule.get("keywords", [])
            if any(kw in rule_kws for kw in all_matched_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None, intent

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None, intent

    def _get_embedding(self, text: str) -> list[float] | None:
        if not self.embedding_api:
            return None
        try:
            payload = {"texts": [text]}
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.embedding_api_key}",
            }
            with httpx.Client(timeout=30) as client:
                resp = client.post(self.embedding_api, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            embeddings = data.get("data") or data.get("embeddings") or []
            if embeddings and isinstance(embeddings, list):
                first = embeddings[0]
                if isinstance(first, dict):
                    return first.get("embedding") or first.get("vector")
                elif isinstance(first, list):
                    return first
            return None
        except Exception as e:
            logger.warning(f"[SemanticSplitter] embedding failed: {e}")
            return None

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _keyword_fallback(self, body: dict) -> RoutingDecision:
        from .keyword import KeywordSplitter
        k = KeywordSplitter(config=self.config, keywords=self.keywords)
        return k.detect(body)

    def _extract_user_text(self, body: dict) -> str:
        texts = []
        for msg in body.get("messages", []):
            if msg.get("role") != "user":
                continue
            c = msg.get("content", "")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
        return " ".join(texts)