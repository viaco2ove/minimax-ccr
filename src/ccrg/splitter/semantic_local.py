"""
SemanticSplitterLocal — 本地模型版语义分流器。
"""

import logging
from typing import Any, Literal

import numpy as np

from .base import RoutingDecision, Splitter

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

        # 从 keywords.json 预取关键词参考（用于语义匹配）
        wflow = self.keywords.get("workflow_intent", {})
        self._chat_keywords: list[str] = wflow.get("chat_intention", [])
        self._task_keywords: list[str] = wflow.get("intention_analyze", [])
        self._keyword_emb_chat: np.ndarray | None = None
        self._keyword_emb_task: np.ndarray | None = None

        logger.info(f"SemanticSplitterLocal configured: model={self.model_name}, threshold={self.threshold}, "
                    f"chat_kws={len(self._chat_keywords)}, task_kws={len(self._task_keywords)}")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配意图并返回路由决策"""
        text = self._extract_user_text(body)
        if not text.strip():
            return self._keyword_fallback(body)

        model = self._load_model()
        user_emb = model.encode(text)

        scores: dict[str, float] = {}

        # 1. 与 keywords.json 关键词向量比较
        chat_emb = self._encode_keywords(model, self._chat_keywords)
        task_emb = self._encode_keywords(model, self._task_keywords)
        if chat_emb is not None:
            scores["chat"] = self._cosine(user_emb, chat_emb)
        if task_emb is not None:
            scores["task"] = self._cosine(user_emb, task_emb)

        # 2. 与候选描述向量比较
        for cand in self.candidates:
            cand_emb = model.encode(cand["description"])
            score = self._cosine(user_emb, cand_emb)
            scores[cand["intent"]] = max(scores.get(cand["intent"]), score)

        logger.debug(f"SemanticSplitterLocal scores: {scores}")

        best = max(scores, key=scores.get)
        best_score = scores[best]

        if best_score < self.threshold:
            return self._keyword_fallback(body)

        logger.info(f"SemanticSplitterLocal matched intent={best} (score={best_score:.3f})")

        # 解析路由
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])
        route, fallback = self._resolve_route(best, rules)
        return RoutingDecision(
            intent=best,
            route=route,
            matched_rule="semantic_routing",
            matched_reason=f"score={best_score:.3f}",
            fallback=fallback,
        )

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

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            import time
            import shutil
            start = time.time()
            logger.info(f"[SemanticSplitterLocal] 正在下载/加载模型: {self.model_name}，请稍候（首次可能较慢）...")
            try:
                kwargs = {"device": self.device}
                if self.trust_remote_code:
                    kwargs["trust_remote_code"] = True
                self._model = SentenceTransformer(self.model_name, **kwargs)
                elapsed = time.time() - start
                logger.info(f"[SemanticSplitterLocal] 模型加载完成，耗时 {elapsed:.1f}s")
            except FileNotFoundError as e:
                # 某些模型（如 jina-embeddings-v3）自定义代码下载不完整，清除缓存重试
                logger.warning(f"[SemanticSplitterLocal] 模型文件缺失，尝试清除缓存重试: {e}")
                cache_dir = self._get_model_cache_dir()
                if cache_dir and cache_dir.exists():
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    logger.info(f"[SemanticSplitterLocal] 已清除缓存: {cache_dir}")
                # 使用 snapshot_download 完整下载
                try:
                    from huggingface_hub import snapshot_download
                    local_path = snapshot_download(
                        repo_id=self.model_name,
                        local_files_only=False,
                        resume_download=False,
                    )
                    logger.info(f"[SemanticSplitterLocal] 完整下载到: {local_path}")
                    kwargs = {"device": self.device}
                    if self.trust_remote_code:
                        kwargs["trust_remote_code"] = True
                    self._model = SentenceTransformer(local_path, **kwargs)
                    elapsed = time.time() - start
                    logger.info(f"[SemanticSplitterLocal] 模型加载完成，耗时 {elapsed:.1f}s")
                except Exception as e2:
                    logger.error(f"[SemanticSplitterLocal] 重试后仍失败: {e2}，请更换模型（推荐 BAAI/bge-m3）")
                    raise
            except Exception as e:
                logger.error(f"[SemanticSplitterLocal] 模型加载失败: {e}，请更换模型（推荐 BAAI/bge-m3 或 shibing624/text2vec-base-chinese）")
                raise
        return self._model

    def _get_model_cache_dir(self):
        """获取模型缓存目录"""
        try:
            from pathlib import Path
            from huggingface_hub import snapshot_download
            # 尝试获取缓存路径
            import os
            default_cache = Path.home() / ".cache" / "huggingface" / "hub"
            # models--{org}--{model} 格式
            parts = self.model_name.split("/")
            if len(parts) == 2:
                model_cache_name = f"models--{parts[0]}--{parts[1]}"
                cache_path = default_cache / model_cache_name
                if cache_path.exists():
                    return cache_path
        except Exception:
            pass
        return None

    def _encode_keywords(self, model, keywords: list[str]) -> np.ndarray | None:
        """将关键词列表合并为一条文本，编码为向量"""
        if not keywords:
            return None
        # 关键词用空格连接（适合短文本 embedding）
        combined = " ".join(keywords)
        return model.encode(combined)

    @staticmethod
    def _cosine(a, b) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

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