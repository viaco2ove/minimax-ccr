"""
SSE Client - 负责与 CCRG 交互，获取流式响应。

设计原则：
- stream=true: 实时返回每个 chunk（通过 StreamingResponse body_iterator）
- stream=false: 等待流结束，收集所有 chunks

sse_client <-> openai_translator <-> CCRG
"""

import json
import logging
from pathlib import Path
from typing import AsyncGenerator

from .openai_translator import AnthropicToOpenAISSEConverter

logger = logging.getLogger("ccrg")


def _load_log_config() -> dict:
    """加载 log_config.json"""
    config_path = Path(__file__).parent.parent.parent / "log_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


_log_config = _load_log_config()


class FakeRequest:
    """包装请求体，模拟 FastAPI Request"""

    def __init__(self, json_body: dict):
        self._json = json_body

    async def json(self):
        return self._json


async def _stream_wrapper(ccrg_handler, transformed_body: dict) -> AsyncGenerator[bytes, None]:
    """流式包装器：调用 CCRG handler，yield 每个 SSE chunk

    用于 stream=true，实时返回每个 chunk
    """
    fake_request = FakeRequest(transformed_body)
    resp = await ccrg_handler(fake_request)

    logger.debug(f"[SSE_CLIENT] CCRG returned: {type(resp).__name__}")

    converter = AnthropicToOpenAISSEConverter(transformed_body.get("model", ""))
    async for raw_chunk in resp.body_iterator:
        events = converter.convert_chunk(raw_chunk)
        for event in events:
            yield event


async def collect_request(ccrg_handle_request, transformed_body: dict) -> tuple[list[bytes], str]:
    """非流式请求：等待流结束，收集所有 chunks

    Returns:
        tuple: (chunks list, model_name)
    """
    logger.debug(f"[SSE_CLIENT] stream=false, calling CCRG")

    fake_request = FakeRequest(transformed_body)
    resp = await ccrg_handle_request(fake_request)

    logger.debug(f"[SSE_CLIENT] CCRG returned: {type(resp).__name__}")

    chunks = []
    chunk_log = _log_config.get("SSE_CLIENT_CHUNK_LOG", _log_config.get("SSE_CLIENT", False))
    try:
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
            if chunk_log:
                logger.debug(f"[SSE_CLIENT] collected chunk: {len(chunk)} bytes")
    except Exception as e:
        logger.error(f"[SSE_CLIENT] Error collecting chunks: {e}")

    logger.debug(f"[SSE_CLIENT] Total chunks collected: {len(chunks)}")

    return chunks, transformed_body.get("model", "")
