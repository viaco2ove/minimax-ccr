"""
SemanticSplitter — 基于语义向量的工作流意图分流器。

使用 embedding 模型将用户输入转为向量，与预定义的意图候选做余弦相似度匹配。
依赖配置中的 embedding 端点和候选 intent 定义。
"""

import json
import logging
import math
from typing import Any, Literal

import httpx

from .base import Splitter

logger = logging.getLogger("ccrg")


class SemanticSplitter(Splitter):
    """基于语义向量相似度检测工作流意图：chat 或 task"""

    # 默认意图候选（可由配置覆盖）
    DEFAULT_CANDIDATES = [
        {
            "intent": "task",
            "description": "代码开发、任务规划、分析执行、问题解决等目的明确的工作",
        },
        {
            "intent": "chat",
            "description": "日常闲聊、问答、解释说明等非任务导向的对话",
        },
    ]

    # 相似度阈值：低于此值则回退到 keyword fallback
    DEFAULT_THRESHOLD = 0.6

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None):
        self.config = config or {}
        self.keywords = keywords
        self.registry = registry

        # 从配置读取 splitter 配置
        splitter_cfg = self.config.get("routing", {}).get("splitter", {})
        sem_cfg = splitter_cfg.get("semantic_splitter", {})
        self.embedding_provider = sem_cfg.get("embedding_provider", "minimax")
        self.embedding_model = sem_cfg.get("embedding_model", "embo-01")
        self.embedding_api = sem_cfg.get("embedding_api", "")
        self.embedding_api_key = sem_cfg.get("embedding_api_key", "")

        self.candidates = splitter_cfg.get("candidates", self.DEFAULT_CANDIDATES)
        self.threshold = splitter_cfg.get("threshold", self.DEFAULT_THRESHOLD)
        self.fallback_splitter: Splitter | None = None

        if self.embedding_api:
            logger.info(
                f"SemanticSplitter configured: embedding={self.embedding_provider}:{self.embedding_model}, "
                f"threshold={self.threshold}, candidates={len(self.candidates)}"
            )
        else:
            logger.warning("SemanticSplitter: no embedding_api configured, will use keyword fallback")

    def detect_intent(self, body: dict) -> Literal["chat", "task"]:
        """基于语义向量相似度检测意图"""
        user_text = self._extract_user_text(body)
        if not user_text.strip():
            logger.debug("SemanticSplitter: empty user text, using keyword fallback")
            return self._keyword_fallback(body)

        # 获取 embedding
        emb = self._get_embedding(user_text)
        if emb is None:
            return self._keyword_fallback(body)

        # 计算与每个候选的相似度
        scores: dict[str, float] = {}
        for cand in self.candidates:
            cand_emb = self._get_candidate_embedding(cand)
            if cand_emb is not None:
                sim = self._cosine_similarity(emb, cand_emb)
                scores[cand["intent"]] = sim
            else:
                scores[cand["intent"]] = 0.0

        logger.debug(f"SemanticSplitter intent scores: {scores}")

        best_intent = max(scores, key=scores.get)
        best_score = scores.get(best_intent, 0.0)

        if best_score < self.threshold:
            logger.info(
                f"SemanticSplitter best_score={best_score:.3f} < threshold={self.threshold}, "
                f"falling back to keyword splitter"
            )
            return self._keyword_fallback(body)

        logger.info(f"SemanticSplitter matched intent={best_intent} (score={best_score:.3f})")
        return best_intent

    def _get_embedding(self, text: str) -> list[float] | None:
        """调用 embedding API 获取文本向量"""
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

            # 解析不同 embedding API 的响应格式
            # 支持 MiniMax / OpenAI 兼容格式
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
        """获取候选意图的预存 embedding（通过 description 实时生成）"""
        desc = candidate.get("description", "")
        return self._get_embedding(desc)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算余弦相似度"""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _keyword_fallback(self, body: dict) -> Literal["chat", "task"]:
        """当 semantic 匹配失败时，回退到 keyword splitter"""
        if self.fallback_splitter is None:
            from .keyword import KeywordSplitter

            self.fallback_splitter = KeywordSplitter(
                config=self.config,
                keywords=self.keywords,
                registry=self.registry,
            )
        return self.fallback_splitter.detect_intent(body)

    def _extract_user_text(self, body: dict) -> str:
        """提取用户消息文本"""
        texts = []
        for msg in body.get("messages", []):
            role = msg.get("role", "")
            if role != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
        return " ".join(texts)