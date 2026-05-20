"""
KeywordSplitter — 基于关键词的工作流意图分流器。
"""

import logging
import re
from typing import Any, Literal

from .base import Splitter

logger = logging.getLogger("ccrg")


class KeywordSplitter(Splitter):
    """基于关键词检测工作流意图：chat 或 task"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None):
        self.keywords = keywords

    def detect_intent(self, body: dict) -> Literal["chat", "task"]:
        """检测用户意图

        Args:
            body: 请求体（OpenAI 或 Anthropic 格式）

        Returns:
            "chat" 或 "task"
        """
        workflow_keywords = self.keywords.get("workflow_intent", {})
        chat_keywords = workflow_keywords.get("chat_intention", [])
        task_keywords = workflow_keywords.get("intention_analyze", [])

        user_text = self._extract_user_text(body).lower()

        chat_score = sum(1 for kw in chat_keywords if self._word_match(kw, user_text))
        task_score = sum(1 for kw in task_keywords if self._word_match(kw, user_text))

        logger.debug(f"KeywordSplitter: chat={chat_score}, task={task_score}")

        if task_score > chat_score:
            if task_score > 0:
                matched = [kw for kw in task_keywords if self._word_match(kw, user_text)]
                logger.info(f"KeywordSplitter matched task keywords: {matched}")
            return "task"

        if chat_score > 0:
            matched = [kw for kw in chat_keywords if self._word_match(kw, user_text)]
            logger.info(f"KeywordSplitter matched chat keywords: {matched}")
        return "chat"

    def _word_match(self, kw: str, text: str) -> bool:
        """单词边界匹配，避免子串误匹配（如 'hi' 命中 'think'）"""
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        return bool(re.search(pattern, text))

    def _extract_user_text(self, body: dict) -> str:
        """提取用户消息文本"""
        texts = []

        for msg in body.get("messages", []):
            role = msg.get("role", "")
            if role != "user":
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))

        return " ".join(texts)
