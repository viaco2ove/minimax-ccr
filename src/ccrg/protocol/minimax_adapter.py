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
        """转换请求，额外处理 MiniMax 的 system 长度、思考开关与推理强度

        思考开关（两者任一命中即关闭思考）：
          - capabilities.thinking == False
          - reasoning_effort == "none"（即"零思考"）
        命中时注入 MiniMax 官方关思考参数 thinking={"type":"disabled"}。
        注意：
          - MiniMax-M3（非 Flash）支持 disabled，可真正关闭思考；
          - MiniMax-M3.1-Flash 官方强制开启思考，传 disabled 会返回 400，
            框架会在 400 后自动剥离 thinking 降级（见 main._strip_unsupported_features），
            即对 M3.1-Flash 仍无法真正关闭思考（上游限制）。

        未命中时走 output_config.effort（low/medium/high/xhigh/max）控制思考深度。
        """
        # 先捕获客户端原始的 effort（父类会因 codeplan_anthropic 剥离 output_config）
        client_effort = None
        req_oc = request.get("output_config") if isinstance(request, dict) else None
        if isinstance(req_oc, dict):
            client_effort = req_oc.get("effort")

        # 先执行父类的通用转换（codeplan_anthropic 处理）
        result = super().transform_request(request, provider_config)

        provider_caps = provider_config.get("capabilities", {}) or {}
        provider_effort_raw = (
            provider_config.get("reasoning_effort")
            or provider_caps.get("reasoning_effort")
        )
        thinking_flag = provider_caps.get("thinking")

        # —— 思考开关：reasoning_effort="none" 或 capabilities.thinking=False → 关闭思考 ——
        want_no_thinking = (
            thinking_flag is False
            or (isinstance(provider_effort_raw, str)
                and provider_effort_raw.strip().lower() == "none")
        )
        if want_no_thinking:
            result["thinking"] = {"type": "disabled"}
            # 思考关闭后 effort 无意义，移除可能残留的 output_config
            result.pop("output_config", None)
        else:
            # —— 思考深度：output_config.effort（仅当思考开启时） ——
            # 父类对 codeplan_anthropic 会剥离 output_config，这里为 MiniMax 重新注入 effort
            # 策略：客户端已带合法 effort 时尊重客户端；否则用 provider 配置兜底
            valid_efforts = {"low", "medium", "high", "xhigh", "max"}
            provider_effort = provider_effort_raw if provider_effort_raw in valid_efforts else None
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
