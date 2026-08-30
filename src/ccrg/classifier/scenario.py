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

        # lv1. compact 场景（优先级最高）
        if self._has_compact(request):
            return "compact"

        # lv1. long_context 场景
        token_count = self._estimate_tokens(request)
        threshold = scenarios_config.get("long_context", {}).get("threshold", 60000)
        if token_count > threshold:
            return "long_context"

        # lv2. image 场景（优先级提升：在 thinking 之前检测，因为 Claude Code 总是带 thinking 参数）
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



        return None

    def _has_compact(self, request: dict) -> bool:
        """检查是否为"纯 /compact 命令"（仅检查最后一条 user 消息，避免历史消息干扰）。

        新版 Claude Code 会把 /compact 命令、执行回显及后续用户真实发言合并进
        同一条 user 消息（如 req_cfd6a5cc：block[3] 为命令包装、block[5]="继续"
        为真实发言）。仅当除命令包装/系统回显块外无真实用户发言时，才判定为
        compact，避免用户后续发言被吞、跳过意图分析。
        """
        system_markers = (
            "<command-name>", "<command-message>", "<command-args>",
            "<local-command-stdout>", "<local-command-caveat>",
            "[Request interrupted by user]", "<system-reminder>",
        )
        messages = request.get("messages", [])
        # 只检查最后一条 user 消息，避免历史消息中的 /compact 影响后续请求
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            texts: list[str] = []
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]

            has_compact_cmd = False
            has_real_speech = False
            for t in texts:
                if "/compact" in t:
                    has_compact_cmd = True
                stripped = t.strip()
                if not stripped:
                    continue
                if any(m in stripped for m in system_markers):
                    continue
                has_real_speech = True

            if has_compact_cmd:
                return not has_real_speech
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
        """检查最后一条用户消息是否包含图片（不检查历史消息）"""
        messages = request.get("messages", [])

        # 只检查最后一条 user 消息
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        return True
            elif isinstance(content, dict) and content.get("type") == "image":
                return True
            break  # 只检查最后一条 user 消息

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
