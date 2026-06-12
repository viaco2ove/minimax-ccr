"""
FallbackRouter - 负责按顺序尝试多个 provider，直到成功或全部失败。

核心原则：
- 一次 CLI 请求，只发一个请求到一个 provider
- provider 返回 200 = 成功，直接返回
- provider 返回错误 = 尝试下一个
- 所有 provider 都失败 = 返回错误给 CLI

debug 日志仅在 log_level == "debug" 时输出。
"""

import logging
from typing import AsyncGenerator, Optional

from .req_cli_pre import clean_request

# 使用 "ccrg" namespace，与 main.py 一致，确保 debug 日志能正确输出
logger = logging.getLogger("ccrg")


def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（简单按字符/4估算）"""
    return len(text) // 4


def _calc_msgs_tokens(msgs: list) -> int:
    """计算 messages 的总 token 估算值"""
    total = 0
    for msg in msgs:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text = block.get("text", "") or ""
                        total += _estimate_tokens(str(text))
                    elif block.get("type") == "image":
                        # 图片按固定 token 估算
                        total += 1000
        elif isinstance(content, str):
            total += _estimate_tokens(content)
    return total


def _get_provider_max_context(route: str) -> int | None:
    """从 route 字符串解析 provider 名称，返回其 max_context 配置"""
    # route 格式: "provider:model"
    if ":" in route:
        prov_name = route.split(":")[0]
        # 从 main.py 导入 _config
        from .. import main as main_module
        cfg = getattr(main_module, '_config', None)
        if cfg and cfg.providers:
            prov_config = cfg.providers.get(prov_name)
            if prov_config:
                caps = prov_config.get('capabilities', {})
                return caps.get("max_context")
    return None


class FallbackRouter:
    """按顺序尝试 route_list 中的 provider，成功则停止，失败则继续下一个"""

    def __init__(self, route_list: list[str], request_id: str, step_name: str):
        self.route_list = route_list
        self.request_id = request_id
        self.step_name = step_name
        self.current_index = 0

    def get_route_list(self) -> list[str]:
        return self.route_list

    def get_current_route(self) -> Optional[str]:
        if self.current_index < len(self.route_list):
            return self.route_list[self.current_index]
        return None

    def log_route_hit(self, hit_type: str, detail: str):
        """路由命中来源，仅 debug 时打印"""
        logger.debug(f"[FallbackRouter] [RouteList]: [{hit_type}] {detail}")
        logger.debug(f"[FallbackRouter] [RouteList]: {self.route_list}")

    async def call_provider_streaming(
        self,
        call_fn,
        msgs: list,
    ) -> AsyncGenerator[bytes, None]:
        """按顺序尝试所有 provider，直到成功

        Yields:
            每个 chunk 直接 yield 给调用方
        """
        for i, route in enumerate(self.route_list):
            self.current_index = i
            logger.debug(f"[FallbackRouter] CurrRoute [index] {i} [routeName] {route}")
            logger.info(f"[{self.request_id}] Trying {route} for {self.step_name}")

            # Debug: 标记即将发送 curl 请求（实际请求体在 call_provider_streaming 中打印）
            logger.debug(f"[FallbackRouter] [REQ] [CURL]7 route={route}, step={self.step_name}")

            # Debug: 计算 msgs_tokens 并检查是否超过 provider 的 max_context
            msgs_tokens = _calc_msgs_tokens(msgs)
            max_context = _get_provider_max_context(route)
            logger.debug(f"[FallbackRouter] [msgs_tokens] {msgs_tokens}, max_context={max_context}")

            # 如果超过 max_context，跳过该 provider
            if max_context and msgs_tokens > max_context:
                logger.debug(f"[FallbackRouter] [CHECK_RESULT] [NEET_NEXT] true [WHY] exceed max message tokens: {msgs_tokens} > {max_context}")
                # 如果是最后一个 provider，返回错误
                if i == len(self.route_list) - 1:
                    logger.error(f"[FallbackRouter] All providers exceed max context ({msgs_tokens} tokens), last provider {route} skipped")
                    raise RuntimeError(f"All providers exceed max context ({msgs_tokens} tokens)")
                logger.warning(f"[{self.request_id}] {route} exceeds max context ({msgs_tokens} > {max_context}), trying next...")
                continue

            # Debug: 记录清理空字符前的 messages 预览
            if logger.isEnabledFor(logging.DEBUG):
                # 预处理清理（不影响原始 msgs）
                msgs_preview = []
                for m in msgs:
                    content = m.get("content", [])
                    if isinstance(content, list):
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "text":
                                text = b.get("text", "")
                                if text is not None and not str(text).strip():
                                    msgs_preview.append({"type": "text", "text": ""})
                if msgs_preview:
                    logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 before route={route}, empty_blocks={msgs_preview}")

            had_chunk = False
            try:
                # 预处理：清理空字符后再发送
                cleaned_msgs = clean_request({"messages": msgs}, self.request_id, route).get("messages", msgs)
                async for chunk in call_fn(route, cleaned_msgs, self.step_name):
                    had_chunk = True
                    yield chunk

                # async for 正常结束 = provider 返回 200 = 成功
                logger.debug(f"[FallbackRouter] [RESULT] [STATUS] 200 OK")
                logger.debug(f"[FallbackRouter] [{self.request_id}] {self.step_name} succeeded with {route}")
                return

            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "rate limit" in error_msg.lower():
                    error_type = "rate_limit_exceeded"
                elif "context length" in error_msg.lower():
                    error_type = "context_length_exceeded"
                else:
                    error_type = "provider_error"

                logger.debug(f"[FallbackRouter] [RESULT] [STATUS] ERROR: {error_type}")
                logger.debug(f"[FallbackRouter] [CHECK_RESULT] [NEET_NEXT] True [WHY] {error_msg}")
                logger.warning(f"[{self.request_id}] {route} failed for {self.step_name}: {e}, trying next...")
                continue

            # 正常结束但没有 chunk → stream 为空/解析失败，主动触发 fallback
            if not had_chunk:
                logger.warning(f"[{self.request_id}] {route} returned 200 but produced no chunks, trying next...")
                continue

        logger.error(f"[FallbackRouter] All {len(self.route_list)} providers failed for {self.step_name}")
        logger.error(f"[{self.request_id}] All {len(self.route_list)} providers failed for {self.step_name}")
