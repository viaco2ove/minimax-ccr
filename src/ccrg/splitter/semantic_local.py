"""
SemanticSplitterLocal — 本地模型版语义分流器。
"""

import logging
from typing import Any, Literal

import numpy as np

from .base import Splitter

logger = logging.getLogger("ccrg")


class SemanticSplitterLocal(Splitter):
    """使用本地 sentence-transformers 模型做语义分流"""

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

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None):
        self.keywords = keywords
        self.config = config or {}

        cfg = self.config.get("routing", {}).get("splitter", {}).get("semantic_splitter", {})
        self.model_name = cfg.get("model_name", "BAAI/bge-m3")
        self.threshold = cfg.get("threshold", 0.5)
        self.device = cfg.get("device", "cpu")
        self.trust_remote_code = cfg.get("trust_remote_code", False)
        self.candidates = cfg.get("candidates", self.DEFAULT_CANDIDATES)
        self._model = None  # 延迟加载 SentenceTransformer

        logger.info(f"SemanticSplitterLocal configured: model={self.model_name}, threshold={self.threshold}")

    def detect_intent(self, body: dict) -> Literal["chat", "task"]:
        text = self._extract_user_text(body)
        if not text.strip():
            return self._keyword_fallback(body)

        model = self._load_model()
        user_emb = model.encode(text)

        best, best_score = "chat", 0.0
        for cand in self.candidates:
            cand_emb = model.encode(cand["description"])
            score = self._cosine(user_emb, cand_emb)
            if score > best_score:
                best, best_score = cand["intent"], score

        logger.debug(f"SemanticSplitterLocal: best={best}({best_score:.3f})")

        if best_score < self.threshold:
            return self._keyword_fallback(body)

        logger.info(f"SemanticSplitterLocal matched intent={best} (score={best_score:.3f})")
        return best

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            import time
            start = time.time()
            logger.info(f"[SemanticSplitterLocal] 正在下载/加载模型: {self.model_name}，请稍候（首次可能较慢）...")
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                trust_remote_code=self.trust_remote_code,
            )
            elapsed = time.time() - start
            logger.info(f"[SemanticSplitterLocal] 模型加载完成，耗时 {elapsed:.1f}s")
        return self._model

    @staticmethod
    def _cosine(a, b) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _keyword_fallback(self, body: dict) -> Literal["chat", "task"]:
        from .keyword import KeywordSplitter
        k = KeywordSplitter(config=self.config, keywords=self.keywords)
        return k.detect_intent(body)

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