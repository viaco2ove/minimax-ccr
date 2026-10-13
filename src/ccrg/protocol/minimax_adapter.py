"""
MiniMax 协议适配器 — 基于 Anthropic 协议，针对 MiniMax 官网接口做特殊处理。
"""

from .anthropic_adapter import AnthropicAdapter, logger


class MiniMaxAdapter(AnthropicAdapter):
    """MiniMax 协议适配器

    继承 AnthropicAdapter，复用 codeplan_anthropic 的所有处理逻辑，
    额外针对 MiniMax 官网接口的限制做处理：
    - system 字符串有 8000 字符限制，超出会返回 400 invalid params
    - M3.1 通过 output_config.effort 控制推理强度（Anthropic 兼容接口）
    """

    MAX_SYSTEM_LENGTH = 7000  # 留一些余量，官方限制 8000

    def transform_request(self, request: dict, provider_config: dict) -> dict:
        """转换请求，额外处理 MiniMax 的 system 长度与推理强度"""
        # 先捕获客户端原始的 effort（父类会因 codeplan_anthropic 剥离 output_config）
        client_effort = None
        req_oc = request.get("output_config") if isinstance(request, dict) else None
        if isinstance(req_oc, dict):
            client_effort = req_oc.get("effort")

        # 先执行父类的通用转换（codeplan_anthropic 处理）
        result = super().transform_request(request, provider_config)

        # MiniMax M3.1 通过 output_config.effort 控制推理强度（Anthropic 兼容接口）
        # 父类对 codeplan_anthropic 会剥离 output_config，这里为 MiniMax 重新注入 effort
        # 策略：客户端已带合法 effort 时尊重客户端；否则用 provider 配置兜底
        valid_efforts = {"low", "medium", "high", "xhigh", "max"}
        provider_effort = (
            provider_config.get("reasoning_effort")
            or provider_config.get("capabilities", {}).get("reasoning_effort")
        )
        if provider_effort and provider_effort not in valid_efforts:
            logger.warning(
                f"[MiniMaxAdapter] reasoning_effort={provider_effort!r} 非法，已忽略"
                f"（合法值: low/medium/high/xhigh/max）"
            )
            provider_effort = None

        if provider_effort or client_effort:
            output_config = result.get("output_config")
            if not isinstance(output_config, dict):
                effort = client_effort if (client_effort in valid_efforts) else provider_effort
                if effort:
                    result["output_config"] = {"effort": effort}
            elif "effort" not in output_config:
                # 客户端已带 output_config 但无 effort，用 provider 配置补
                if provider_effort:
                    output_config["effort"] = provider_effort

        # MiniMax 对 system 字符串有 8000 字符限制，超出会返回 400 invalid params
        if "system" in result and isinstance(result["system"], str):
            system_str = result["system"]
            if len(system_str) > self.MAX_SYSTEM_LENGTH:
                result["system"] = system_str[:self.MAX_SYSTEM_LENGTH]

        return result
