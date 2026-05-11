"""
关键词分类器 — 从用户消息中提取关键词。
"""

import logging
from typing import Any

logger = logging.getLogger("ccrg.classifier.keyword")


class KeywordClassifier:
    """关键词分类器"""

    def classify(self, request: dict, rules: list[dict]) -> list[str]:
        """根据配置的关键词规则，从请求中提取命中的关键词"""
        matched_keywords = []

        # 收集用户消息文本
        user_text = self._extract_user_text(request)

        for rule in rules:
            rule_keywords = rule.get("keywords", [])
            for keyword in rule_keywords:
                if keyword.lower() in user_text.lower():
                    matched_keywords.append(keyword)

        return matched_keywords

    def _extract_user_text(self, request: dict) -> str:
        """提取用户消息文本"""
        texts = []

        # system prompt
        system = request.get("system", "")
        if isinstance(system, list):
            for item in system:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
        elif isinstance(system, str):
            texts.append(system)

        # messages — 只取用户消息
        for msg in request.get("messages", []):
            role = msg.get("role", "")
            if role != "user":
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        # 忽略 image 等其他类型

        return " ".join(texts)

    def extract_tags(self, request: dict, rules: list[dict]) -> list[str]:
        """提取关键词标签"""
        return self.classify(request, rules)
