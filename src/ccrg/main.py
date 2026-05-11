"""
CCRG FastAPI 主入口。
"""

import logging
import time
import uuid
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import load_config
from .protocol import AnthropicAdapter, OpenAIAdapter, ProtocolAdapter
from .classifier.scenario import ScenarioClassifier
from .classifier.tool_type import ToolTypeClassifier
from .classifier.keyword import KeywordClassifier
from .provider import ProviderRegistry
from .router import RoutingEngine
from .types import GatewayConfig, ProviderConfig, RequestTags

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ccrg")


# ── 全局状态 ──────────────────────────────────────────────────────

app: FastAPI | None = None
_config: GatewayConfig | None = None
_registry: ProviderRegistry | None = None
_routing_engine: RoutingEngine | None = None
_classifier_scenario = ScenarioClassifier()
_classifier_tool = ToolTypeClassifier()


def init_app(config_path: str | None = None) -> FastAPI:
    """初始化 FastAPI 应用"""
    global _config, _registry, _routing_engine, app

    _config = load_config(config_path)
    _registry = ProviderRegistry(_config)
    _routing_engine = RoutingEngine(_config)

    app = FastAPI(title="Claude Code Router Gateway")

    @app.post("/v1/messages")
    async def handle_messages(request: Request):
        return await _handle_request(request)

    @app.get("/health")
    async def health():
        providers = list(_config.providers.keys()) if _config else []
        return {"status": "ok", "providers": providers}

    @app.get("/")
    async def root():
        priority = _config.routing.get("priority", []) if _config else []
        providers = list(_config.providers.keys()) if _config else []
        return {
            "name": "Claude Code Router Gateway",
            "version": "0.1.0",
            "providers": providers,
            "routing_priority": priority,
        }

    return app


async def _handle_request(request: Request) -> Response:
    """处理 /v1/messages 请求"""
    request_id = f"req_{uuid.uuid4().hex[:8]}"
    start_time = time.time()

    try:
        body = await request.json()
    except Exception:
        error_resp = JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request", "message": "Invalid JSON"}}
        )
        return error_resp

    logger.info(f"[{request_id}] Received request: model={body.get('model')}, stream={body.get('stream')}")

    # 1. 分类请求
    tags = _classify_request(body)

    # 2. 路由决策
    route_result = _routing_engine.route(tags)
    logger.info(f"[{request_id}] Routed to {route_result.provider}:{route_result.model} "
                f"via {route_result.matched_rule} ({route_result.matched_reason})")

    # 3. 获取 provider 配置
    provider_config = _registry.get(route_result.provider)
    if not provider_config:
        error_resp = JSONResponse(
            status_code=500,
            content={"error": {"type": "provider_error", "message": f"Unknown provider: {route_result.provider}"}}
        )
        return error_resp

    # 4. 选择 adapter
    adapter: ProtocolAdapter
    if provider_config.protocol == "codeplan_anthropic":
        adapter = AnthropicAdapter()
    elif provider_config.protocol == "chat_openai":
        adapter = OpenAIAdapter()
    elif provider_config.protocol == "mmx":
        # mmx: 走本地 mmx_provider.py，也用 Anthropic 格式
        adapter = AnthropicAdapter()
    else:
        error_resp = JSONResponse(
            status_code=500,
            content={"error": {"type": "adapter_error", "message": f"Unknown protocol: {provider_config.protocol}"}}
        )
        return error_resp

    # 5. 转换请求
    transformed_request = adapter.transform_request(body, _provider_config_to_dict(provider_config))

    # 6. 获取目标 URL
    target_url = adapter.get_target_url(_provider_config_to_dict(provider_config), route_result.model)
    if not target_url.startswith("http"):
        target_url = f"http://{target_url}"

    # 7. 发送请求（带 fallback）
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider_config.api_key}",
    }

    timeout = _config.server.get("timeout_ms", 600000) / 1000

    # 先尝试主 provider，然后是 fallback
    providers_to_try = [(route_result.provider, route_result.model, adapter)] + [
        (fb_provider, fb_model, _get_adapter_for_provider(fb_provider))
        for fb_provider, fb_model in route_result.fallback_chain
    ]

    last_error = None
    for prov_name, model, prov_adapter in providers_to_try:
        try:
            # 更新请求中的 model
            req_for_provider = dict(transformed_request)
            req_for_provider["model"] = model

            logger.info(f"[{request_id}] Calling {prov_name} at {target_url}")

            # 获取 provider 协议
            prov_config = _registry.get(prov_name)
            prov_protocol = prov_config.protocol if prov_config else "codeplan_anthropic"

            async with httpx.AsyncClient(timeout=timeout) as client:
                if body.get("stream"):
                    # 流式请求
                    return await _handle_streaming(
                        request_id, client, target_url, headers, req_for_provider,
                        prov_adapter, provider_protocol=prov_protocol
                    )
                else:
                    # 非流式请求
                    response = await client.post(target_url, json=req_for_provider, headers=headers)
                    response.raise_for_status()

                    resp_data = response.json()
                    transformed_resp = prov_adapter.transform_json_response(resp_data)

                    latency = time.time() - start_time
                    logger.info(f"[{request_id}] Success from {prov_name}, latency={latency:.3f}s")

                    return JSONResponse(content=transformed_resp)

        except httpx.HTTPStatusError as e:
            logger.warning(f"[{request_id}] {prov_name} returned {e.response.status_code}")
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"[{request_id}] {prov_name} error: {e}")
            last_error = e
            continue

    # 所有 provider 都失败
    latency = time.time() - start_time
    logger.error(f"[{request_id}] All providers failed after {latency:.3f}s")
    error_msg = str(last_error) if last_error else "All providers failed"
    return JSONResponse(
        status_code=502,
        content={"error": {"type": "upstream_error", "message": error_msg}}
    )


async def _handle_streaming(
    request_id: str,
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    request: dict,
    adapter: ProtocolAdapter,
    provider_protocol: str = "codeplan_anthropic"
) -> StreamingResponse:
    """处理流式请求"""

    from .protocol.openai_sse import OpenAISSEConverter

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        try:
            async with client.stream("POST", url, json=request, headers=headers) as response:
                response.raise_for_status()

                # 如果是 OpenAI provider，需要转换 SSE
                converter = None
                if provider_protocol == "chat_openai":
                    model = request.get("model", "")
                    converter = OpenAISSEConverter(model)

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue

                    # 转发非 data 行（如注释）
                    if not line.startswith("data: "):
                        yield f"{line}\n".encode("utf-8")
                        continue

                    # OpenAI SSE 转换
                    if converter:
                        raw_chunk = line.encode("utf-8")
                        events = converter.convert_chunk(raw_chunk)
                        for event in events:
                            yield event
                    else:
                        # Anthropic 直接转发
                        yield f"{line}\n".encode("utf-8")

        except Exception as e:
            logger.error(f"[{request_id}] Streaming error: {e}")
            error_json = json.dumps({"error": {"type": "upstream_error", "message": str(e)}})
            yield f"data: {error_json}\n\n".encode("utf-8")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


def _provider_config_to_dict(provider: ProviderConfig) -> dict:
    """将 ProviderConfig 转换为 dict（供 adapter 使用）"""
    return {
        "name": provider.name,
        "api_base_url": provider.api_base_url,
        "api_key": provider.api_key,
        "protocol": provider.protocol,
        "models": provider.models,
        "capabilities": provider.capabilities,
        "cost_tier": provider.cost_tier,
        "default_params": provider.default_params,
        "retry": provider.retry,
    }


def _get_adapter_for_provider(provider_name: str) -> ProtocolAdapter:
    """获取 provider 对应的 adapter"""
    provider = _registry.get(provider_name)
    if not provider:
        return AnthropicAdapter()

    if provider.protocol == "codeplan_anthropic":
        return AnthropicAdapter()
    elif provider.protocol == "chat_openai":
        return OpenAIAdapter()
    elif provider.protocol == "mmx":
        return AnthropicAdapter()
    else:
        return AnthropicAdapter()


def _classify_request(request: dict) -> RequestTags:
    """对请求进行分类"""
    global _config, _classifier_scenario, _classifier_tool

    # Scenario 分类
    config_dict = _config.__dict__ if hasattr(_config, "__dict__") else {"routing": getattr(_config, "routing", {})}
    tags = _classifier_scenario.extract_tags(request, config_dict)

    # Tool 类型分类
    tool_types, tool_details = _classifier_tool.extract_tags(request)
    tags.tool_types = tool_types
    tags.tool_details = tool_details

    # 关键词分类
    keyword_rules = _config.routing.get("keyword_routing", {}).get("rules", []) if _config else []
    keyword_classifier = KeywordClassifier()
    tags.keywords = keyword_classifier.extract_tags(request, keyword_rules)

    return tags


def run(host: str | None = None, port: int | None = None, config_path: str | None = None):
    """启动 Gateway"""
    import uvicorn

    init_app(config_path)

    host = host or (_config.server.get("host", "127.0.0.1") if _config else "127.0.0.1")
    port = port or (_config.server.get("port", 3458) if _config else 3458)

    logger.info(f"Starting CCRG on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()