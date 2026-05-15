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
            logger.debug(f"[FallbackRouter] [REQ] [CURL] route={route}, step={self.step_name}")

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
