"""
Anthropic 协议适配器 — 透传 + 微调。
"""

import json
import re
from typing import Any

from .base import ProtocolAdapter

logger = __import__("logging").getLogger("ccrg")


class AnthropicAdapter(ProtocolAdapter):
    """Anthropic 协议适配器

    当 Provider 使用 Anthropic 协议时，直接透传请求和响应，
    只需要做一些微调（如合并 default_params、清理 system-reminder）。
    """

    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """对 Anthropic 格式请求做微调"""
        result = dict(request)

        # 1. 合并 default_params
        default_params = provider_config.get("default_params", {})
        for key, value in default_params.items():
            if key not in result:
                result[key] = value

        # 2. 确保 max_tokens 存在
        if "max_tokens" not in result:
            result["max_tokens"] = 4096

        # 3. 清理 system-reminder
        result = _strip_system_reminders(result)

        # 4. 如果 provider 不支持 thinking，剥离 thinking 字段
        capabilities = provider_config.get("capabilities", {})
        if not capabilities.get("thinking", False) and "thinking" in result:
            del result["thinking"]

        # 5. 如果 provider 不支持 vision，剥离 image 内容块
        if not capabilities.get("vision", False):
            result = _strip_images(result)

        # 6. 处理 output_config.effort 参数，确保值有效
        if "output_config" in result:
            output_config = result["output_config"]
            if isinstance(output_config, dict) and "effort" in output_config:
                effort = output_config["effort"]
                # 豆包等 API 只接受 low, medium, high, max
                valid_efforts = {"low", "medium", "high", "max"}
                if effort not in valid_efforts:
                    # 把 xhigh 映射到 high，其他无效值映射到 medium
                    if effort == "xhigh":
                        output_config["effort"] = "high"
                    else:
                        output_config["effort"] = "medium"

        # 7. 对部分 Anthropic 兼容的 provider，剥离可能导致 400 的字段
        #    codeplan_anthropic 协议的 provider (minimax, doubao, qianfan) 对某些
        #    Anthropic 原生参数支持不完整，直接透传会触发 "invalid params"
        protocol = provider_config.get("protocol", "")
        if protocol in ("codeplan_anthropic", "mmx"):
            # 7a. 剥离 output_config — 这是 Claude Code 特有参数，非原生 Anthropic Messages API
            if "output_config" in result:
                del result["output_config"]

            # 7b. 清理 thinking 中的 budget_tokens — 部分 provider 不支持
            if "thinking" in result and isinstance(result["thinking"], dict):
                thinking = result["thinking"]
                # 如果 thinking 类型不是 "enabled"，直接删除整个 thinking
                if thinking.get("type") not in ("enabled", "disabled"):
                    del result["thinking"]
                elif thinking.get("type") == "disabled":
                    del result["thinking"]
                elif "budget_tokens" in thinking:
                    # budget_tokens 可能不被支持，移除
                    thinking = dict(thinking)
                    del thinking["budget_tokens"]
                    result["thinking"] = thinking

            # 7c. 清理 tool_choice — 部分 provider 不支持 "any", "tool" 类型
            if "tool_choice" in result:
                tc = result["tool_choice"]
                if isinstance(tc, dict):
                    tc_type = tc.get("type", "")
                    if tc_type in ("any", "tool"):
                        # 映射 "any"/"tool" → "auto"
                        result["tool_choice"] = {"type": "auto"}

            # 7d. system 列表格式兼容 — 将 content blocks 列表转为纯字符串
            #     部分 provider 不支持 system 为 list[{"type":"text","text":"..."}]
            if "system" in result and isinstance(result["system"], list):
                parts = []
                for item in result["system"]:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text", "")
                        if text:  # 跳过空文本块
                            parts.append(text)
                    elif isinstance(item, str):
                        parts.append(item)
                result["system"] = "\n\n".join(parts) if parts else ""

            # 7e. 清理 tools 中的 cache_control — 部分 provider 不支持
            if "tools" in result and isinstance(result["tools"], list):
                cleaned_tools = []
                for tool in result["tools"]:
                    if isinstance(tool, dict) and "cache_control" in tool:
                        tool = {k: v for k, v in tool.items() if k != "cache_control"}
                    cleaned_tools.append(tool)
                result["tools"] = cleaned_tools

            # 7f. 限制 max_tokens — MiniMax 等 provider 的 output budget 过大会导致
            #     context window 超限 (input + max_tokens > context_limit)
            #     MiniMax 128K context，建议 max_tokens 上限 32K？实际测试是30000 左右!!!!
            # 改为 22000， 超过会报错： invalid params, context window exceeds limit (2013)"
            # 输入总 token + max_tokens ≤ 模型上下文窗口上限。 所以既不是不是32k 也不是30k 也不是 22000。
            # MAX_OUTPUT_TOKENS 只是 用于限制最大值。 capabilities.capabilities + default_params.max_tokens（MAX_OUTPUT_TOKENS） 要小于模型上下文窗口上限
            MAX_OUTPUT_TOKENS = 22000
            if "max_tokens" in result:
                try:
                    current_max = int(result["max_tokens"])
                    logger.debug(f"Routed to transform_request  客户端 max_tokens : {result["max_tokens"]}")
                    if current_max > MAX_OUTPUT_TOKENS:
                        result["max_tokens"] = MAX_OUTPUT_TOKENS
                except (ValueError, TypeError):
                    pass

            # 7g. 截断过长的 system prompt — MiniMax 等 provider 的 context window 有限
            #     MiniMax 128K context，约 32K tokens，系统 prompt 27K 字符已超过安全线
            MAX_SYSTEM_LENGTH = 8000  # 与 mmx_provider.py 保持一致
            if "system" in result and isinstance(result["system"], str):
                system = result["system"]
                if len(system) > MAX_SYSTEM_LENGTH:
                    result["system"] = system[:MAX_SYSTEM_LENGTH]

        return result

    def get_target_url(self, provider_config: dict, model: str | None = None) -> str:
        """获取 Anthropic 格式的 URL"""
        base = provider_config["api_base_url"].rstrip("/")
        if not base.endswith("/v1/messages"):
            base += "/v1/messages"
        return base

    def transform_json_response(self, response: dict, context: dict | None = None) -> dict:
        """Anthropic 响应直接透传"""
        return response

    def transform_response_headers(self, headers: dict) -> dict:
        """Anthropic 响应头直接透传"""
        return headers

    def needs_sse_event_prefix(self) -> bool:
        """Anthropic SSE 需要 event: 前缀"""
        return False

    def get_sse_event_name(self) -> str:
        return ""


def _strip_system_reminders(obj: Any) -> Any:
    """递归移除 system-reminder 块，并清理空内容"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "system":
                result[k] = _strip_system_reminders(v)
            elif k != "cache_control":
                result[k] = _strip_system_reminders(v)
        return result
    elif isinstance(obj, list):
        # 过滤空内容块
        filtered = []
        for item in obj:
            stripped = _strip_system_reminders(item)
            # 跳过空 text block
            if isinstance(stripped, dict) and stripped.get("type") == "text":
                text = stripped.get("text", "")
                if not text or not text.strip():
                    continue
            filtered.append(stripped)
        return filtered
    elif isinstance(obj, str):
        # 移除 <system-reminder>...</system-reminder> 块
        return re.sub(r'<system-reminder>.*?</system-reminder>', '', obj, flags=re.DOTALL).strip()
    return obj


def extract_system(request: dict) -> str:
    """从 Anthropic 请求中提取 system prompt"""
    system = request.get("system", "")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for item in system:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return ""


def _strip_images(request: dict) -> dict:
    """从请求中移除 image 内容块，保留文本描述"""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return request

    changed = False
    new_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    changed = True
                    # 用文本占位替代，保留上下文连贯性
                    new_content.append({"type": "text", "text": "[image]"})
                else:
                    new_content.append(block)
            if changed:
                msg = dict(msg, content=new_content)
        new_messages.append(msg)

    if changed:
        request = dict(request, messages=new_messages)
    return request
