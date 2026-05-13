"""
场景分类器 — 识别 think/web_search/image/long_context/background。
"""

import json
import logging
from typing import Any

from ..types import RequestTags

logger = logging.getLogger("ccrg.classifier.scenario")


class ScenarioClassifier:
    """场景分类器"""

    def classify(self, request: dict, config: dict) -> str | None:
        """从请求中识别场景类型"""
        routing_config = config.get("routing", {})
        scenarios_config = routing_config.get("scenarios", {})

        # 0. compact 场景（优先级最高）
        if self._has_compact(request):
            return "compact"

        # 1. image 场景（优先级提升：在 thinking 之前检测，因为 Claude Code 总是带 thinking 参数）
        if self._has_image_content(request):
            return "image"

        # 2. thinking 场景
        if request.get("thinking"):
            return "think"

        # 3. web_search 场景
        if self._has_web_search(request):
            return "web_search"

        # 4. background 场景（haiku 模型）
        model = request.get("model", "")
        if "haiku" in model.lower():
            return "background"

        # 5. long_context 场景
        token_count = self._estimate_tokens(request)
        threshold = scenarios_config.get("long_context", {}).get("threshold", 60000)
        if token_count > threshold:
            return "long_context"

        return None

    def _has_compact(self, request: dict) -> bool:
        """检查是否包含 /compact 命令（仅检查最后一条 user 消息，避免历史消息干扰）"""
        messages = request.get("messages", [])
        # 只检查最后一条 user 消息，避免历史消息中的 /compact 影响后续请求
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                if "/compact" in content:
                    return True
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        if "/compact" in block.get("text", ""):
                            return True
            break  # 只检查最后一条 user 消息
        return False

    def _has_web_search(self, request: dict) -> bool:
        """检查是否包含 web_search tools"""
        tools = request.get("tools", [])
        for tool in tools:
            tool_type = tool.get("type", "")
            if tool_type.startswith("web_search"):
                return True
        return False

    def _has_image_content(self, request: dict) -> bool:
        """检查请求中是否包含图片"""
        # 检查 system
        system = request.get("system", [])
        if isinstance(system, list):
            for item in system:
                if isinstance(item, dict) and item.get("type") == "image":
                    return True

        # 检查 messages
        for msg in request.get("messages", []):
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        return True
            elif isinstance(content, dict) and content.get("type") == "image":
                return True

        return False

    def _estimate_tokens(self, request: dict) -> int:
        """估算 token 数（简化实现：用字符数除以 4）"""
        text = json.dumps(request, ensure_ascii=False)
        return len(text) // 4

    def extract_tags(self, request: dict, config: dict) -> RequestTags:
        """提取完整的请求标签"""
        tags = RequestTags()

        tags.scenario = self.classify(request, config)
        tags.has_thinking = bool(request.get("thinking"))
        tags.has_web_search = self._has_web_search(request)
        tags.has_images = self._has_image_content(request)
        tags.token_count = self._estimate_tokens(request)
        tags.model_hint = request.get("model")

        return tags
