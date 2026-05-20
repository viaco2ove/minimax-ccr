"""请求分流模块 - 根据关键词等对请求进行模型分流"""

from .workflow import WorkflowSplitter

__all__ = ["WorkflowSplitter"]