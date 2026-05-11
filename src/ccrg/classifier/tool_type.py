"""
Tool 类型分类器 — 从消息中提取 tool 调用信息。
"""

import json
import logging
from typing import Any

from ..types import RequestTags, ToolDetail

logger = logging.getLogger("ccrg.classifier.tool_type")


class ToolTypeClassifier:
    """Tool 类型分类器

    从消息中提取 tool 调用信息，支持模式匹配：
    - ToolName: 精确匹配 tool name
    - ToolName(subcommand): 匹配 name + subcommand 前缀
    - ToolName(subcommand*): 通配符匹配
    """

    def classify(self, request: dict) -> tuple[list[str], list[ToolDetail]]:
        """从请求中提取 tool 类型和详细信息"""
        tool_types = []
        tool_details = []
        seen_names = set()

        messages = request.get("messages", [])

        for msg in messages:
            content = msg.get("content", "")
            role = msg.get("role", "")

            # Anthropic 格式
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    block_type = block.get("type", "")

                    # tool_result — 表示 tool 执行完毕
                    if block_type == "tool_result":
                        tool_use_id = block.get("tool_use_id", "")
                        # 尝试从之前的 tool_use 找到名称
                        name = self._resolve_tool_name_from_messages(
                            tool_use_id, messages
                        )
                        if name and name not in seen_names:
                            seen_names.add(name)
                            tool_types.append(name)
                            tool_details.append(ToolDetail(
                                name=name,
                                subcommand="",
                                raw_input=block
                            ))

                    # tool_use — 表示请求 tool 调用
                    elif block_type == "tool_use":
                        name = block.get("name", "")
                        if name and name not in seen_names:
                            seen_names.add(name)
                            tool_types.append(name)
                            subcommand = self._extract_subcommand(name, block.get("input", {}))
                            tool_details.append(ToolDetail(
                                name=name,
                                subcommand=subcommand,
                                raw_input=block.get("input", {})
                            ))

                    # thinking 块（不作为 tool）
                    elif block_type == "thinking":
                        pass

                    # text 块
                    elif block_type == "text":
                        pass

            # 检查 tool_calls（OpenAI 格式或 assistant 消息）
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    tool_types.append(name)
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}

                    subcommand = self._extract_subcommand(name, args)
                    tool_details.append(ToolDetail(
                        name=name,
                        subcommand=subcommand,
                        raw_input=args
                    ))

        return tool_types, tool_details

    def _resolve_tool_name_from_messages(self, tool_use_id: str, messages: list) -> str | None:
        """从之前的消息中查找 tool_use_id 对应的 tool 名称"""
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("id") == tool_use_id:
                            return block.get("name")
        return None

    def _extract_subcommand(self, tool_name: str, tool_input: dict) -> str:
        """提取 tool 的子命令（仅对特定 tool 类型有意义）"""
        if tool_name == "Bash":
            return tool_input.get("command", "")
        elif tool_name == "Read":
            return tool_input.get("file_path", "")
        elif tool_name == "Write":
            return tool_input.get("file_path", "")
        elif tool_name == "Edit":
            return tool_input.get("file_path", "")
        elif tool_name == "Glob":
            return tool_input.get("pattern", "")
        elif tool_name == "Grep":
            return tool_input.get("path", "")
        return ""

    def extract_tags(self, request: dict) -> tuple[list[str], list[ToolDetail]]:
        """提取 tool 相关标签"""
        return self.classify(request)
