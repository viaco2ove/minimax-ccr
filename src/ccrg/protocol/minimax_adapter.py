"""
MiniMax 协议适配器 — 基于 Anthropic 协议，针对 MiniMax 官网接口做特殊处理。
"""

from .anthropic_adapter import AnthropicAdapter


class MiniMaxAdapter(AnthropicAdapter):
    """MiniMax 协议适配器

    继承 AnthropicAdapter，复用 codeplan_anthropic 的所有处理逻辑，
    额外针对 MiniMax 官网接口的限制做处理：
    - system 字符串有 8000 字符限制，超出会返回 400 invalid params
    """

    MAX_SYSTEM_LENGTH = 7000  # 留一些余量，官方限制 8000

    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """转换请求，额外处理 MiniMax 的 system 长度限制"""
        # 先执行父类的通用转换（codeplan_anthropic 处理）
        result = super().transform_request(request, provider_config)

        # MiniMax 对 system 字符串有 8000 字符限制，超出会返回 400 invalid params
        if "system" in result and isinstance(result["system"], str):
            system_str = result["system"]
            if len(system_str) > self.MAX_SYSTEM_LENGTH:
                result["system"] = system_str[:self.MAX_SYSTEM_LENGTH]

        return result
