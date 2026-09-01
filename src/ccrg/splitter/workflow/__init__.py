"""workflow 独立分流器包。

在 splitter/workflow/ 内自包含实现 workflow 阶段分流，不复用下级
semantic_local / llm / keyword splitter 的代码。
"""

from .workflow_splitter import WorkflowSplitter

__all__ = ["WorkflowSplitter"]
