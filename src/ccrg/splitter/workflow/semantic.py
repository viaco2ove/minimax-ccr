"""workflow 包内独立语义策略（不复用下级 semantic_local.py）。

配置直接从 config['workflow']['workflow_splitter']['semantic_splitter'] 读取，
使用本地 sentence-transformers embedding 模型计算相似度判定 workflow 阶段。
"""

import logging
import sys
from typing import Any

import numpy as np

from ..base import RoutingDecision, resolve_workflow_stage
from .common import extract_user_text, get_workflow_splitter_config

logger = logging.getLogger("ccrg")

# workflow 判定涉及的全部 category（与 workflow_intent 关键词分组一致）
_WORKFLOW_CATEGORIES = [
    "chat_intention",
    "intention_analyze",
    "problem_analyze",
    "solution_plan",
    "execute_solve",
]


class WorkflowSemanticStrategy:
    """使用本地 embedding 模型做 workflow 阶段语义分流"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        self.config = config or {}
        self.keywords = keywords or {}
        # usage_stats 不用于语义策略

        cfg = get_workflow_splitter_config(self.config).get("semantic_splitter", {})
        self.model_name = cfg.get("model_name", "moka-ai/m3e-small")
        self.threshold = cfg.get("threshold", 0.5)
        self.device = cfg.get("device", "cpu")
        self.trust_remote_code = cfg.get("trust_remote_code", False)
        self.hf_endpoint = cfg.get("hf_endpoint", "")
        self._model = None
        self._load_failed = False

        logger.info(f"[WorkflowSemanticStrategy] configured: model={self.model_name}, threshold={self.threshold}")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配关键词并返回路由决策"""
        text = extract_user_text(body)
        if not text.strip():
            return self._fallback_no_text(body)

        if self._load_failed:
            return self._fallback_no_text(body)

        model = self._load_model()
        if model is None:
            return self._fallback_no_text(body)

        try:
            user_emb = model.encode(text)
        except Exception as e:
            logger.error(f"[WorkflowSemanticStrategy] model.encode failed: {e}")
            return self._fallback_no_text(body)

        best_scores = self._best_scores_per_category(model, user_emb)
        matched = self._match_keywords(model, user_emb)

        # 判定 chat/task 意图（用最高分归属法，对齐下级 semantic_local）
        chat_cats = ["chat_intention"]
        task_cats = ["intention_analyze", "problem_analyze", "solution_plan", "execute_solve"]
        chat_best = max((best_scores.get(c, 0.0) for c in chat_cats), default=0.0)
        task_best = max((best_scores.get(c, 0.0) for c in task_cats), default=0.0)
        intent = "chat" if chat_best > task_best else "task"

        # 合并所有命中关键词用于路由匹配
        all_matched_kws = []
        for kws in matched.values():
            for item in kws:
                all_matched_kws.append(item[0] if isinstance(item, tuple) else item)

        route_str, fb = self._resolve_route_from_hits(all_matched_kws, intent)

        workflow_stage = resolve_workflow_stage(best_scores, threshold=self.threshold)

        logger.debug(
            f"[WorkflowSemanticStrategy] intent={intent}, route={route_str}, "
            f"fb={fb}, workflow_stage={workflow_stage}, matched={all_matched_kws}"
        )
        return RoutingDecision(
            intent=intent,
            route=route_str,
            matched_rule="workflow_semantic_routing",
            matched_reason=f"keywords={all_matched_kws}" if all_matched_kws else "no_match",
            fallback=fb,
            workflow_stage=workflow_stage,
        )

    def _match_keywords(self, model, user_emb: np.ndarray) -> dict:
        """计算用户输入与每个 category 关键词的相似度，返回命中（超过阈值）的关键词"""
        result = {}
        wflow = self.keywords.get("workflow_intent", {})

        for category in _WORKFLOW_CATEGORIES:
            kw_list = wflow.get(category, []) or []
            # problem_analyze 为空时用 analyze_plan 顶替
            if category == "problem_analyze" and not kw_list:
                kw_list = wflow.get("analyze_plan", []) or []
            if not kw_list:
                continue

            scores = []
            for kw in kw_list:
                kw_emb = model.encode(kw)
                score = self._cosine(user_emb, kw_emb)
                scores.append((kw, score))

            scores.sort(key=lambda x: x[1], reverse=True)
            top_kws = [(kw, round(score, 3)) for kw, score in scores[:3] if score >= self.threshold]
            if top_kws:
                result[category] = top_kws

        return result

    def _best_scores_per_category(self, model, user_emb: np.ndarray) -> dict:
        """计算每个 category 的最高相似度（含不过阈值的），用于 intent 与 workflow_stage 判定"""
        result = {}
        wflow = self.keywords.get("workflow_intent", {})

        for category in _WORKFLOW_CATEGORIES:
            kw_list = wflow.get(category, []) or []
            if category == "problem_analyze" and not kw_list:
                kw_list = wflow.get("analyze_plan", []) or []
            if not kw_list:
                continue

            best = 0.0
            for kw in kw_list:
                kw_emb = model.encode(kw)
                score = self._cosine(user_emb, kw_emb)
                if score > best:
                    best = score
            result[category] = round(best, 3)

        return result

    def _resolve_route_from_hits(self, matched_kws: list[str], intent: str) -> tuple[str, list[str] | None]:
        """用命中的关键词匹配 keyword_routing.rules 找路由（对齐下级 semantic_local._resolve_route_from_keywords）"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        for rule in rules:
            rule_kws = rule.get("keywords", [])
            if any(kw in rule_kws for kw in matched_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None

    def _load_model(self):
        """加载本地 embedding 模型（离线优先，联网兜底）。失败后永久禁用，避免反复加载拖死进程。"""
        if self._load_failed:
            return self._model
        if self._model is not None:
            return self._model

        import os
        import shutil
        import time
        import traceback

        logger.info(f"[WorkflowSemanticStrategy] _load_model called, model={self.model_name}, device={self.device}")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            logger.warning(f"[WorkflowSemanticStrategy] sentence_transformers not available: {e} | sys.path[:3]={sys.path[:3]}")
            self._load_failed = True
            return None
        except Exception as e:
            logger.error(f"[WorkflowSemanticStrategy] import sentence_transformers failed: {e}\n{traceback.format_exc()}")
            self._load_failed = True
            return None

        start = time.time()
        cache_dir = self._get_model_cache_dir()
        is_cached = cache_dir is not None and cache_dir.exists()

        try:
            if is_cached:
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                from huggingface_hub import snapshot_download
                local_path = snapshot_download(repo_id=self.model_name, local_files_only=True)
                logger.info(f"[WorkflowSemanticStrategy] 本地缓存路径: {local_path}")
                kwargs = {"device": self.device}
                if self.trust_remote_code:
                    kwargs["trust_remote_code"] = True
                self._model = SentenceTransformer(local_path, **kwargs)
            else:
                if self.hf_endpoint:
                    os.environ["HF_ENDPOINT"] = self.hf_endpoint
                kwargs = {"device": self.device}
                if self.trust_remote_code:
                    kwargs["trust_remote_code"] = True
                self._model = SentenceTransformer(self.model_name, **kwargs)
            elapsed = time.time() - start
            action = "加载" if is_cached else "下载并加载"
            logger.info(f"[WorkflowSemanticStrategy] 模型{action}完成，耗时 {elapsed:.1f}s")
        except FileNotFoundError as e:
            logger.warning(f"[WorkflowSemanticStrategy] 模型文件缺失，尝试清除缓存重试: {e}")
            cache_dir = self._get_model_cache_dir()
            if cache_dir and cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
            if self.hf_endpoint:
                os.environ["HF_ENDPOINT"] = self.hf_endpoint
            try:
                from huggingface_hub import snapshot_download
                local_path = snapshot_download(repo_id=self.model_name, local_files_only=False, resume_download=False)
                kwargs = {"device": self.device}
                if self.trust_remote_code:
                    kwargs["trust_remote_code"] = True
                self._model = SentenceTransformer(local_path, **kwargs)
                logger.info(f"[WorkflowSemanticStrategy] 完整下载到: {local_path}")
            except Exception as e2:
                logger.error(f"[WorkflowSemanticStrategy] 重试后仍失败: {e2}，请更换模型（推荐 BAAI/bge-m3）")
                self._model = None
                self._load_failed = True
                return None
        except Exception as e:
            logger.error(f"[WorkflowSemanticStrategy] 模型加载失败: {e}，请更换模型（推荐 BAAI/bge-m3 或 shibing624/text2vec-base-chinese）")
            self._model = None
            self._load_failed = True
            return None

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

    def _fallback_no_text(self, body: dict) -> RoutingDecision:
        """无有效文本/模型不可用时，退回包内关键词策略"""
        from .keyword import WorkflowKeywordStrategy
        return WorkflowKeywordStrategy(config=self.config, keywords=self.keywords).detect(body)
