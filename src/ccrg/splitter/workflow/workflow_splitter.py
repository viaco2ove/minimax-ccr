"""workflow 独立分流器主类。

依据 config['workflow']['workflow_splitter']（enabled / active_strategy /
semantic_splitter / llm_splitter）选择包内独立策略判定 workflow 阶段，
不复用下级 semantic_local / llm / keyword splitter 的代码。
"""

import logging
from typing import Any

from ..base import RoutingDecision, Splitter
from .common import get_workflow_splitter_config
from .keyword import WorkflowKeywordStrategy
from .llm import WorkflowLLMStrategy
from .semantic import WorkflowSemanticStrategy

logger = logging.getLogger("ccrg")


class WorkflowSplitter(Splitter):
    """workflow 阶段分流器：根据 workflow.workflow_splitter.active_strategy
    分派到包内独立的 semantic / llm / keyword 策略。
    """

    def __init__(self, config: dict[str, Any] | None, keywords: dict,
                 registry: Any = None, usage_stats: Any = None):
        self.config = config or {}
        self.keywords = keywords or {}
        self.registry = registry
        self.usage_stats = usage_stats

        wf_cfg = get_workflow_splitter_config(self.config)
        self.active_strategy = wf_cfg.get("active_strategy", "keyword_splitter")

        # 包内独立策略实例（不复用下级 splitter）
        self._semantic = WorkflowSemanticStrategy(self.config, self.keywords, registry, usage_stats)
        self._llm = WorkflowLLMStrategy(self.config, self.keywords, registry, usage_stats)
        self._keyword = WorkflowKeywordStrategy(self.config, self.keywords, registry, usage_stats)

        logger.info(f"[WorkflowSplitter] created: active_strategy={self.active_strategy}")

    def detect(self, body: dict) -> RoutingDecision:
        """按 active_strategy 分派到对应独立策略"""
        if self.active_strategy == "semantic_splitter":
            return self._semantic.detect(body)
        if self.active_strategy == "llm_splitter":
            return self._llm.detect(body)
        return self._keyword.detect(body)

    def detect_intent(self, body: dict) -> str:
        """向后兼容旧接口：返回 'chat' / 'task'"""
        return self.detect(body).intent

    def _load_model(self):
        """预加载 semantic 模型（供 init_app 预热，避免首次请求才下载）"""
        if self.active_strategy == "semantic_splitter":
            self._model = self._semantic._load_model()
        else:
            self._model = None
        return self._model
