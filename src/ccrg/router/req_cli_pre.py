"""
req_cli_pre.py - CLI 请求预处理器
负责在发送请求给 provider 之前，清理空字符等无效内容

判断是否有空值 -> 清除空值 -> 发给大模型
"""

import logging
from typing import Any

logger = logging.getLogger("ccrg")


def clean_request(body: dict, request_id: str | None = None, route: str | None = None) -> dict:
    """清理请求体中的空 text blocks 和 thinking blocks

    判断是否json -> 是否有空值 -> 清除空值 -> 发给大模型

    Args:
        body: 原始请求体
        request_id: 可选的请求 ID，用于日志

    Returns:
        清理后的请求体
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body

    changed = False
    new_messages = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                block_type = block.get("type", "")
                # 跳过空 text block
                if block_type == "text":
                    text = block.get("text", "")
                    if text and str(text).strip():
                        new_content.append(block)
                    else:
                        changed = True
                    continue
                # 跳过 thinking block（部分 provider 不支持）
                if block_type == "thinking":
                    changed = True
                    continue
                new_content.append(block)
            msg = dict(msg, content=new_content)
        new_messages.append(msg)

    if changed:
        result = dict(body, messages=new_messages)
        if route:
            logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 route={route}, req_id={request_id or ''}, cleaned=true")
        elif request_id:
            logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 req_id={request_id}, cleaned=true")
        else:
            logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 cleaned=true")
        return result

    if route:
        logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 route={route}, req_id={request_id or ''}, cleaned=false")
    elif request_id:
        logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 req_id={request_id}, cleaned=false")
    else:
        logger.debug(f"[FallbackRouter] [ReqCleanEmpty] 清理空字符 cleaned=false")
    return body