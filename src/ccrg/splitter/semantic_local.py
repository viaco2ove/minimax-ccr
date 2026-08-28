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
        # 模型加载曾失败（如打包/frozen 环境 sentence_transformers 不可用，或 import 触发原生崩溃）
        # 则永远不再尝试加载，避免单次请求反复 import 把整个进程拖死（闪退）。
        self._load_failed = False

        # 从 keywords.json 预取关键词
        wflow = self.keywords.get("workflow_intent", {})
        self._chat_keywords: list[str] = wflow.get("chat_intention", [])
        self._task_keywords: list[str] = wflow.get("intention_analyze", [])

        logger.info(f"[SemanticSplitterLocal] configured: model={self.model_name}, threshold={self.threshold}")

    def detect(self, body: dict) -> RoutingDecision:
        """基于语义向量匹配关键词并返回路由决策"""
        import traceback
        msgs = body.get("messages", [])
        logger.debug(f"[SemanticSplitterLocal] body: {len(msgs)} messages")
        text = self._extract_user_text(body)
        if not text.strip():
            return self._keyword_fallback(body)

        # 模型曾加载失败（如打包/frozen 环境 sentence_transformers 不可用，或 import 时触发原生崩溃）：
        # 直接降级 keyword 路由，绝不再尝试加载，避免单次请求反复 import 把整个进程拖死（闪退）。
        if self._load_failed:
            return self._keyword_fallback(body)
        model = self._load_model()
        if model is None:
            return self._keyword_fallback(body)

        try:
            user_emb = model.encode(text)
        except Exception as e:
            logger.error(f"[SemanticSplitterLocal] model.encode failed: {e}\n{traceback.format_exc()}")
            return self._keyword_fallback(body)

        # 遍历所有关键词，计算相似度，找出命中的关键词
        matched = self._match_keywords(model, user_emb)

        # 计算每 category 的最高相似度（含不过阈值的），用于 intent 判定
        best_scores = self._best_scores_per_category(model, user_emb)

        # 根据命中关键词解析路由
        route_str, fb, intent = self._resolve_route_from_keywords(matched, best_scores)

        logger.debug(f"[SemanticSplitterLocal] matched :{intent} , route_str:{route_str}, fb:{fb}")
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
            # 1. 必须先定义 kw_list（这行不能丢！不能少！）
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
                kw_emb = model.encode(kw)
                score = self._cosine(user_emb, kw_emb)
                scores.append((kw, score))

            # 按相似度降序排序，取最高分
            scores.sort(key=lambda x: x[1], reverse=True)

            # 只取相似度最高且超过阈值的关键词（最多 3 个），保留分数
            top_kws = [(kw, round(score, 3)) for kw, score in scores[:3] if score >= self.threshold]

            if top_kws:
                result[category] = top_kws

        # 打印带分数的命中结果，按最高分降序排序
        sorted_result = sorted(result.items(), key=lambda x: max(s for _, s in x[1]), reverse=True)
        log_items = [f"{cat}:{kws}" for cat, kws in sorted_result]
        logger.debug(f"[SemanticSplitterLocal] matched Arr: {{{', '.join(log_items)}}}")

        return result

    def _resolve_route_from_keywords(self, matched: dict, best_scores: dict | None = None) -> tuple[str, list[str] | None, str]:
        """根据命中关键词从 keyword_routing.rules 找路由，和 llm_splitter 逻辑一致"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        # 合并所有命中的关键词（每个条目可能是 str 或 (str, float) 元组）
        all_matched_kws = []
        for kws in matched.values():
            for item in kws:
                all_matched_kws.append(item[0] if isinstance(item, tuple) else item)

        # 判断意图：用"最高分归属"法 —— chat 类与 task 类各自的最高相似度，高的归属即 intent。
        # 取代旧的计数法（task 用 2 个 category 求和恒 > chat 的 1 个 category，导致"你好"都判 task）。
        # best_scores 为每 category 的最高分（含不过阈值的）；缺失则视为 0。
        if best_scores:
            chat_cats = ["chat_intention"]
            task_cats = ["intention_analyze", "problem_analyze", "solution_plan", "execute_solve"]
            chat_best = max((best_scores.get(c, 0.0) for c in chat_cats), default=0.0)
            task_best = max((best_scores.get(c, 0.0) for c in task_cats), default=0.0)
            intent = "chat" if chat_best > task_best else "task"
            logger.debug(
                f"[SemanticSplitterLocal] intent by best-score: "
                f"chat_best={chat_best:.3f} task_best={task_best:.3f} -> {intent}"
            )
        else:
            # 兜底：旧计数法（best_scores 不可用时）
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

    def _best_scores_per_category(self, model, user_emb: np.ndarray) -> dict:
        """计算每个 category 的最高相似度（含不过阈值的），用于 intent 判定。

        与 _match_keywords 的 category 遍历逻辑保持一致（problem_analyze 空时用 analyze_plan 顶替）。
        """
        result = {}
        wflow = self.keywords.get("workflow_intent", {})
        categories = ["chat_intention", "intention_analyze", "problem_analyze", "solution_plan", "execute_solve"]

        for category in categories:
            kw_list = wflow.get(category, [])
            if category == "problem_analyze" and not kw_list:
                kw_list = wflow.get("analyze_plan", [])
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

    def _load_model(self):
        if self._load_failed:
            return self._model
        if self._model is None:
            import os
            import time
            import shutil
            import traceback
            logger.info(f"[SemanticSplitterLocal] _load_model called, model={self.model_name}, device={self.device}")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                logger.warning(f"[SemanticSplitterLocal] sentence_transformers not available: {e}")
                logger.warning(f"[SemanticSplitterLocal] sys.path[0:3]={sys.path[:3]}")
                self._model = None
                self._load_failed = True
                return
            except Exception as e:
                logger.error(f"[SemanticSplitterLocal] failed to import sentence_transformers: {e}\n{traceback.format_exc()}")
                self._model = None
                self._load_failed = True
                return
            start = time.time()

            # 检查本地缓存是否存在
            cache_dir = self._get_model_cache_dir()
            is_cached = cache_dir and cache_dir.exists()

            if is_cached:
                logger.info(f"[SemanticSplitterLocal] 正在加载模型: {self.model_name}（从本地缓存，离线模式）...")
            else:
                logger.info(f"[SemanticSplitterLocal] 正在下载模型: {self.model_name}，请稍候（首次可能较慢）...")

            try:
                if is_cached:
                    # 本地有缓存：强制离线加载，跳过 huggingface_hub 的联网版本检查
                    # （否则每次启动都会对仓库每个文件发 HEAD 请求比对 ETag，镜像 504 时卡几十秒）
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    os.environ["TRANSFORMERS_OFFLINE"] = "1"
                    from huggingface_hub import snapshot_download
                    local_path = snapshot_download(
                        repo_id=self.model_name,
                        local_files_only=True,
                    )
                    logger.info(f"[SemanticSplitterLocal] 本地缓存路径: {local_path}")
                    kwargs = {"device": self.device}
                    if self.trust_remote_code:
                        kwargs["trust_remote_code"] = True
                    self._model = SentenceTransformer(local_path, **kwargs)
                else:
                    # 无缓存：联网下载
                    kwargs = {"device": self.device}
                    if self.trust_remote_code:
                        kwargs["trust_remote_code"] = True
                    self._model = SentenceTransformer(self.model_name, **kwargs)
                elapsed = time.time() - start
                action = "加载" if is_cached else "下载并加载"
                logger.info(f"[SemanticSplitterLocal] 模型{action}完成，耗时 {elapsed:.1f}s")
            except FileNotFoundError as e:
                logger.warning(f"[SemanticSplitterLocal] 模型文件缺失，尝试清除缓存重试: {e}")
                cache_dir = self._get_model_cache_dir()
                if cache_dir and cache_dir.exists():
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    logger.info(f"[SemanticSplitterLocal] 已清除缓存: {cache_dir}")
                # 重试前必须关掉离线模式，否则无法联网下载
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
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
                    self._model = None
                    self._load_failed = True
                    return
            except Exception as e:
                logger.error(f"[SemanticSplitterLocal] 模型加载失败: {e}，请更换模型（推荐 BAAI/bge-m3 或 shibing624/text2vec-base-chinese）")
                self._model = None
                self._load_failed = True
                return
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
        joined = " ".join(texts)
        # 剥离 <system-reminder ...>...</system-reminder> 块，减少噪声对语义匹配的干扰
        import re
        joined = re.sub(r"<system-reminder[^>]*>.*?</system-reminder>", " ", joined, flags=re.DOTALL)
        return joined.strip()