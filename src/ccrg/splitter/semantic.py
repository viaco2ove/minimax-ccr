"""
SemanticSplitter — 基于语义向量的工作流意图分流器（API 版）。

使用外部 embedding API 获取向量，通过余弦相似度判断意图并返回路由。
取代 keyword_routing。
"""

import json
import logging
import math
from typing import Any

import httpx

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class SemanticSplitter(Splitter):
    """基于语义向量相似度检测意图并返回路由 — 取代 keyword_routing"""

    DEFAULT_CANDIDATES = [
        {"intent": "task", "description": "代码开发、任务规划、分析执行、问题解决等目的明确的工作"},
        {"intent": "chat", "description": "日常闲聊、问答、解释说明等非任务导向的对话"},
    ]

    DEFAULT_THRESHOLD = 0.6

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None):
        self.config = config or {}
        self.keywords = keywords
        self.registry = registry

        splitter_cfg = self.config.get("routing", {}).get("splitter", {})
        sem_cfg = splitter_cfg.get("semantic_splitter", {})
        self.embedding_api = sem_cfg.get("embedding_api", "")
        self.embedding_api_key = sem_cfg.get("embedding_api_key", "")
        self.candidates = splitter_cfg.get("candidates", self.DEFAULT_CANDIDATES)
        self.threshold = splitter_cfg.get("threshold", self.DEFAULT_THRESHOLD)
        self.fallback_splitter: Splitter | None = None

        # 从 keywords.json 预取关键词
        wflow = self.keywords.get("workflow_intent", {})
        self._chat_keywords: list[str] = wflow.get("chat_intention", [])
        self._task_keywords: list[str] = wflow.get("intention_analyze", [])

        if self.embedding_api:
            logger.info(f"SemanticSplitter configured: api={self.embedding_api}, threshold={self.threshold}")
        else:
            logger.warning("SemanticSplitter: no embedding_api configured, will use keyword fallback")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配意图并返回路由决策"""
        user_text = self._extract_user_text(body)
        if not user_text.strip():
            return self._keyword_fallback(body)

        emb = self._get_embedding(user_text)
        if emb is None:
            return self._keyword_fallback(body)

        scores: dict[str, float] = {}

        # 1. 与 keywords.json 关键词向量比较
        chat_emb = self._get_embedding(" ".join(self._chat_keywords))
        task_emb = self._get_embedding(" ".join(self._task_keywords))
        if chat_emb is not None:
            scores["chat"] = self._cosine(emb, chat_emb)
        if task_emb is not None:
            scores["task"] = self._cosine(emb, task_emb)

        # 2. 与候选描述向量比较
        for cand in self.candidates:
            cand_emb = self._get_candidate_embedding(cand)
            if cand_emb is not None:
                score = self._cosine(emb, cand_emb)
                scores[cand["intent"]] = max(scores.get(cand["intent"]), score)
            else:
                scores[cand["intent"]] = scores.get(cand["intent"], 0.0)

        logger.debug(f"SemanticSplitter scores: {scores}")

        best = max(scores, key=scores.get)
        best_score = scores[best]

        if best_score < self.threshold:
            return self._keyword_fallback(body)

        logger.info(f"SemanticSplitter matched intent={best} (score={best_score:.3f})")

        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])
        route, fallback = self._resolve_route(best, rules)
        return RoutingDecision(
            intent=best,
            route=route,
            matched_rule="semantic_routing",
            matched_reason=f"score={best_score:.3f}",
            fallback=fallback,
        )

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
            logger.warning(f"SemanticSplitter embedding failed: {e}")
            return None

    def _get_candidate_embedding(self, candidate: dict) -> list[float] | None:
        desc = candidate.get("description", "")
        return self._get_embedding(desc)

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

    def _resolve_route(self, intent: str, rules: list[dict]) -> tuple[str, list[str] | None]:
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