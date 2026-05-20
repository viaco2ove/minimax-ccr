"""
Splitter 工厂 — 根据配置创建对应的 splitter 实例。
"""

import logging
from typing import Any

from .base import Splitter
from .keyword import KeywordSplitter

logger = logging.getLogger("ccrg")


class SplitterFactory:
    """Splitter 工厂，根据 active_strategy 创建对应 splitter"""

    _BUILDERS: dict[str, type[Splitter]] = {
        "keyword_splitter": KeywordSplitter,
        "semantic_splitter": None,   # lazily filled
        "llm_splitter": None,         # lazily filled
    }

    @classmethod
    def _ensure_builder(cls, strategy: str) -> None:
        """按需加载指定 builder，避免无关依赖（如 sentence-transformers）被导入"""
        if strategy == "keyword_splitter":
            return  # 已预加载
        if strategy == "semantic_splitter" and cls._BUILDERS.get("semantic_splitter") is None:
            from .semantic_local import SemanticSplitterLocal
            cls._BUILDERS["semantic_splitter"] = SemanticSplitterLocal
        elif strategy == "llm_splitter" and cls._BUILDERS.get("llm_splitter") is None:
            from .llm import LLMSplitter
            cls._BUILDERS["llm_splitter"] = LLMSplitter

    @classmethod
    def create(
        cls,
        active_strategy: str,
        config: dict[str, Any],
        keywords: dict,
        registry: Any = None,
    ) -> Splitter:
        """创建 splitter 实例

        Args:
            active_strategy: 分流策略名（keyword_splitter / semantic_splitter / llm_splitter）
            config: gateway 配置字典（包含 splitter 配置）
            keywords: keywords.json 内容
            registry: ProviderRegistry 实例（用于 llm_splitter 调用外部模型）

        Returns:
            Splitter 实例
        """
        cls._ensure_builder(active_strategy)
        builder = cls._BUILDERS.get(active_strategy)

        if builder is None:
            available = list(cls._BUILDERS.keys())
            logger.warning(
                f"Unknown splitter strategy: '{active_strategy}', "
                f"falling back to 'keyword_splitter'. Available: {available}"
            )
            builder = cls._BUILDERS["keyword_splitter"]

        splitter = builder(config=config, keywords=keywords, registry=registry)
        logger.info(f"Splitter created: {active_strategy}")
        return splitter

    @classmethod
    def available_strategies(cls) -> list[str]:
        """返回所有已注册的分流策略"""
        return list(cls._BUILDERS.keys())