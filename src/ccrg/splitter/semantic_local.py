"""
SemanticSplitterLocal — 本地模型版语义分流器。

使用本地 embedding 模型，计算每个关键词与用户输入的相似度，返回命中的关键词列表。
"""

import logging
from typing import Any

import numpy as np

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class SemanticSplitterLocal(Splitter):
    """使用本地 sentence-transformers 模型做语义分流"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        self.keywords = keywords
        self.config = config or {}
        # usage_stats 不用于 semantic_local

        cfg = self.config.get("routing", {}).get("splitter", {}).get("semantic_splitter", {})
        self.model_name = cfg.get("model_name", "BAAI/bge-m3")
        self.threshold = cfg.get("threshold", 0.5)
        self.device = cfg.get("device", "cpu")
        self.trust_remote_code = cfg.get("trust_remote_code", False)
        self._model = None

        # 从 keywords.json 预取关键词
        wflow = self.keywords.get("workflow_intent", {})
        self._chat_keywords: list[str] = wflow.get("chat_intention", [])
        self._task_keywords: list[str] = wflow.get("intention_analyze", [])

        logger.info(f"[SemanticSplitterLocal] configured: model={self.model_name}, threshold={self.threshold}")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配关键词并返回路由决策"""
        logger.debug(f"[SemanticSplitterLocal] body: {body}")
        text = self._extract_user_text(body)
        if not text.strip():
            return self._keyword_fallback(body)

        model = self._load_model()
        user_emb = model.encode(text)

        # 遍历所有关键词，计算相似度，找出命中的关键词
        matched = self._match_keywords(model, user_emb)

        logger.debug(f"[SemanticSplitterLocal] matched: {matched}")

        # 根据命中关键词解析路由
        route_str, fb, intent = self._resolve_route_from_keywords(matched)
        return RoutingDecision(
            intent=intent,
            route=route_str,
            matched_rule="semantic_routing",
            matched_reason=f"keywords={matched}" if matched else "no_match",
            fallback=fb,
        )

    def _match_keywords(self, model, user_emb: np.ndarray) -> dict:
        """计算用户输入与每个关键词的相似度，返回每个 category 相似度最高的关键词"""
        result = {}

        wflow = self.keywords.get("workflow_intent", {})
        categories = ["chat_intention", "intention_analyze", "problem_analyze", "solution_plan", "execute_solve"]

        for category in categories:
            kw_list = wflow.get(category, [])
            if not kw_list:
                continue

            # 计算所有关键词的相似度
            scores = []
            for kw in kw_list:
                kw_emb = model.encode(kw)
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
                logger.warning(f"[SemanticSplitterLocal] 模型文件缺失，尝试清除缓存重试: {e}")
                cache_dir = self._get_model_cache_dir()
                if cache_dir and cache_dir.exists():
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    logger.info(f"[SemanticSplitterLocal] 已清除缓存: {cache_dir}")
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
        try:
            from pathlib import Path
            default_cache = Path.home() / ".cache" / "huggingface" / "hub"
            parts = self.model_name.split("/")
            if len(parts) == 2:
                model_cache_name = f"models--{parts[0]}--{parts[1]}"
                cache_path = default_cache / model_cache_name
                if cache_path.exists():
                    return cache_path
        except Exception:
            pass
        return None

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