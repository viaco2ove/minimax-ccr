"""
Splitter 模块基类。
"""

from abc import ABC, abstractmethod
from typing import Literal


class Splitter(ABC):
    """分流器抽象基类"""

    @abstractmethod
    def detect_intent(self, body: dict) -> Literal["chat", "task"]:
        """检测工作流意图

        Args:
            body: 请求体（OpenAI 或 Anthropic 格式）

        Returns:
            "chat" — 对话模式（闲聊、问答）
            "task" — 任务模式（代码、规划、分析）
        """
        ...