"""
CCRG FastAPI 主入口。
"""

import json
import logging
import time
import uuid
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import load_config
from .protocol import AnthropicAdapter, OpenAIAdapter, ProtocolAdapter
from .classifier.scenario import ScenarioClassifier
from .classifier.tool_type import ToolTypeClassifier
from .classifier.keyword import KeywordClassifier
from .provider.registry import ProviderRegistry
from .router import RoutingEngine
from .types import GatewayConfig, ProviderConfig, RequestTags
from .usage_stats import get_usage_stats

# 配置日志
import os
from pathlib import Path

log_file = Path("logs/ccrg.log")
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger("ccrg")


# ── 全局状态 ──────────────────────────────────────────────────────

app: FastAPI | None = None
_config: GatewayConfig | None = None
_registry: ProviderRegistry | None = None
_routing_engine: RoutingEngine | None = None
_usage_stats = None
_classifier_scenario = ScenarioClassifier()
_classifier_tool = ToolTypeClassifier()

# ── Dashboard 页面 ──────────────────────────────────────────────

_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CCRG Dashboard</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}
  .header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:24px 32px;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between}
  .header h1{font-size:22px;font-weight:600;color:#f1f5f9}
  .header .badge{background:#3b82f6;color:#fff;padding:4px 12px;border-radius:12px;font-size:12px}
  .container{max-width:1200px;margin:0 auto;padding:24px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px}
  .card .label{font-size:13px;color:#94a3b8;margin-bottom:4px}
  .card .value{font-size:28px;font-weight:700;color:#f1f5f9}
  .card .sub{font-size:12px;color:#64748b;margin-top:4px}
  .section{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:24px}
  .section h2{font-size:16px;font-weight:600;margin-bottom:16px;color:#e2e8f0}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 12px;color:#94a3b8;font-weight:500;border-bottom:1px solid #334155}
  td{padding:10px 12px;border-bottom:1px solid #1e293b}
  tr:hover td{background:#0f172a}
  .tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:500}
  .tag-green{background:#064e3b;color:#6ee7b7}
  .tag-red{background:#450a0a;color:#fca5a5}
  .tag-blue{background:#1e3a5f;color:#93c5fd}
  .bar{height:8px;border-radius:4px;background:#334155;overflow:hidden;margin-top:6px}
  .bar-fill{height:100%;border-radius:4px;transition:width .6s ease}
  .refresh-btn{background:#3b82f6;color:#fff;border:none;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px}
  .refresh-btn:hover{background:#2563eb}
  .empty{text-align:center;color:#64748b;padding:40px;font-size:14px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .loading{animation:spin 1s linear infinite;display:inline-block}
</style>
</head>
<body>
<div class="header">
  <h1>CCRG Dashboard</h1>
  <div style="display:flex;align-items:center;gap:12px">
    <span class="badge" id="auto-badge">Auto 10s</span>
    <button class="refresh-btn" onclick="loadStats()">Refresh</button>
  </div>
</div>
<div class="container">
  <div class="cards" id="summary-cards"></div>
  <div class="section">
    <h2>Today's Usage by Provider</h2>
    <div id="today-table"></div>
  </div>
  <div class="section">
    <h2>All-time Summary</h2>
    <div id="summary-table"></div>
  </div>
</div>
<script>
function fmt(n){return n==null?'-':n.toLocaleString()}
function fmtTokens(n){if(n==null)return'-';if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return n.toString()}
function successTag(s,f){if(s+f===0)return'<span class="tag tag-blue">no data</span>';const r=s/(s+f);return r>=0.9?'<span class="tag tag-green">'+s+'/'+(s+f)+' ('+(r*100).toFixed(0)+'%)</span>':'<span class="tag tag-red">'+s+'/'+(s+f)+' ('+(r*100).toFixed(0)+'%)</span>'}
function barHtml(pct,color){return'<div class="bar"><div class="bar-fill" style="width:'+Math.min(pct,100)+'%;background:'+color+'"></div></div>'}
const COLORS=['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#ef4444'];
function renderSummaryCards(summary){
  const el=document.getElementById('summary-cards');
  const t=summary.total_tokens||0,r=summary.total_requests||0,pCount=Object.keys(summary.providers||{}).length;
  el.innerHTML=`
    <div class="card"><div class="label">Total Requests</div><div class="value">${fmt(r)}</div></div>
    <div class="card"><div class="label">Total Tokens</div><div class="value">${fmtTokens(t)}</div><div class="sub">${fmt(t)} tokens</div></div>
    <div class="card"><div class="label">Providers</div><div class="value">${pCount}</div></div>`;
}
function renderTodayTable(today){
  const el=document.getElementById('today-table');
  const entries=Object.entries(today||{});
  if(!entries.length){el.innerHTML='<div class="empty">No data for today</div>';return}
  let maxTokens=0;entries.forEach(([_,d])=>{if(d.total_tokens>maxTokens)maxTokens=d.total_tokens});
  let html='<table><tr><th>Provider</th><th>Models</th><th>Requests</th><th>Success</th><th>Input</th><th>Output</th><th>Total Tokens</th><th>Avg Latency</th></tr>';
  entries.forEach(([name,d],i)=>{
    const c=COLORS[i%COLORS.length];
    html+=`<tr><td style="font-weight:600;color:${c}">${name}</td><td style="font-size:11px;color:#94a3b8">${(d.models||[]).join(', ')}</td><td>${fmt(d.request_count)}</td><td>${successTag(d.success_count,d.fail_count)}</td><td>${fmtTokens(d.input_tokens)}</td><td>${fmtTokens(d.output_tokens)}</td><td>${fmtTokens(d.total_tokens)}${barHtml(maxTokens?d.total_tokens/maxTokens*100:0,c)}</td><td>${d.avg_latency_ms?d.avg_latency_ms.toFixed(0)+'ms':'-'}</td></tr>`;
  });
  html+='</table>';el.innerHTML=html;
}
function renderSummaryTable(summary){
  const el=document.getElementById('summary-table');
  const entries=Object.entries(summary.providers||{});
  if(!entries.length){el.innerHTML='<div class="empty">No historical data</div>';return}
  let maxTokens=0;entries.forEach(([_,d])=>{if(d.total_tokens>maxTokens)maxTokens=d.total_tokens});
  let html='<table><tr><th>Provider</th><th>Total Requests</th><th>Total Tokens</th></tr>';
  entries.forEach(([name,d],i)=>{
    const c=COLORS[i%COLORS.length];
    html+=`<tr><td style="font-weight:600;color:${c}">${name}</td><td>${fmt(d.total_requests)}</td><td>${fmtTokens(d.total_tokens)}${barHtml(maxTokens?d.total_tokens/maxTokens*100:0,c)}</td></tr>`;
  });
  html+='</table>';el.innerHTML=html;
}
async function loadStats(){
  try{
    const resp=await fetch('/stats');const data=await resp.json();
    renderSummaryCards(data.summary||{});
    renderTodayTable(data.today||{});
    renderSummaryTable(data.summary||{});
  }catch(e){console.error('Failed to load stats:',e)}
}
loadStats();
setInterval(loadStats,10000);
</script>
</body>
</html>"""


def init_app(config_path: str | None = None) -> FastAPI:
    """初始化 FastAPI 应用"""
    global _config, _registry, _routing_engine, _usage_stats, app

    _config = load_config(config_path)
    _registry = ProviderRegistry(_config)
    _routing_engine = RoutingEngine(_config)
    _usage_stats = get_usage_stats(_config)

    app = FastAPI(title="Claude Code Router Gateway")

    @app.post("/v1/messages")
    async def handle_messages(request: Request):
        return await _handle_request(request)

    @app.get("/health")
    async def health():
        providers = list(_config.providers.keys()) if _config else []
        return {"status": "ok", "providers": providers}

    @app.get("/stats")
    async def stats():
        """今日各 provider 的 token 使用统计"""
        today_stats = _usage_stats.get_today() if _usage_stats else {}
        summary = _usage_stats.get_summary() if _usage_stats else {}
        return {
            "today": today_stats,
            "summary": summary,
        }

    @app.get("/dashboard")
    async def dashboard():
        """Token 使用可视化面板"""
        return HTMLResponse(content=_DASHBOARD_HTML)

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

    # 4. 选择 adapter（仅用于日志和初始判断）
    adapter: ProtocolAdapter
    if provider_config.protocol == "codeplan_anthropic":
        adapter = AnthropicAdapter()
    elif provider_config.protocol == "chat_openai":
        adapter = OpenAIAdapter()
    elif provider_config.protocol == "mmx":
        adapter = AnthropicAdapter()
    else:
        error_resp = JSONResponse(
            status_code=500,
            content={"error": {"type": "adapter_error", "message": f"Unknown protocol: {provider_config.protocol}"}}
        )
        return error_resp

    # 5. 发送请求（带 fallback）
    timeout = _config.server.get("timeout_ms", 600000) / 1000

    # 先尝试主 provider，然后是 fallback
    providers_to_try = [(route_result.provider, route_result.model)] + list(route_result.fallback_chain)

    last_error = None
    for prov_name, model in providers_to_try:
        try:
            # 获取 provider 配置
            prov_config = _registry.get(prov_name)
            if not prov_config:
                continue

            # 获取 provider 对应的 adapter
            prov_adapter = _get_adapter_for_provider(prov_name)

            # 为每个 provider 重新转换原始请求（不同协议需要不同格式）
            req_for_provider = prov_adapter.transform_request(body, _provider_config_to_dict(prov_config))
            req_for_provider["model"] = model

            prov_target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
            if not prov_target_url.startswith("http"):
                prov_target_url = f"http://{prov_target_url}"

            prov_headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {prov_config.api_key}",
            }

            logger.info(f"[{request_id}] Calling {prov_name} at {prov_target_url}")

            if body.get("stream"):
                # 流式请求 - 使用独立函数处理，支持 fallback 重试
                return await _handle_streaming_with_fallback(
                    request_id, body, providers_to_try,
                    _provider_config_to_dict, _registry, _get_adapter_for_provider,
                    _usage_stats, route_result.matched_rule, start_time, timeout
                )
            else:
                # 非流式请求
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers)
                    response.raise_for_status()

                    resp_data = response.json()
                    transformed_resp = prov_adapter.transform_json_response(resp_data)

                    latency_ms = (time.time() - start_time) * 1000

                    # 记录 usage stats
                    if _usage_stats and isinstance(transformed_resp, dict):
                        usage = transformed_resp.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)
                        _usage_stats.record(
                            provider=prov_name,
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            latency_ms=latency_ms,
                            success=True,
                            route_rule=route_result.matched_rule,
                        )

                    logger.info(f"[{request_id}] Success from {prov_name}, latency={latency_ms/1000:.3f}s")

                    return JSONResponse(content=transformed_resp)

        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.text[:500]
            except Exception:
                pass
            logger.warning(f"[{request_id}] {prov_name} returned {e.response.status_code}: {error_body}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=route_result.matched_rule,
                )
            last_error = e
            continue
        except Exception as e:
            logger.warning(f"[{request_id}] {prov_name} error: {e}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=route_result.matched_rule,
                )
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


async def _handle_streaming_with_fallback(
    request_id: str,
    original_body: dict,
    providers_to_try: list[tuple[str, str]],
    provider_config_to_dict,  # 函数引用
    registry: ProviderRegistry,
    get_adapter: callable,
    usage_stats,
    matched_rule: str,
    start_time: float,
    timeout: float
) -> StreamingResponse:
    """处理流式请求(带 fallback 支持)"""
    from .protocol.openai_sse import OpenAISSEConverter

    provider_index = [0]  # 用列表包装以便在闭包中修改

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        last_error = None

        while provider_index[0] < len(providers_to_try):
            prov_name, model = providers_to_try[provider_index[0]]
            provider_index[0] += 1

            prov_config = registry.get(prov_name)
            if not prov_config:
                continue

            prov_adapter = get_adapter(prov_name)

            # 为每个 provider 重新转换原始请求（不同协议需要不同格式）
            req_for_provider = prov_adapter.transform_request(original_body, provider_config_to_dict(prov_config))
            req_for_provider["model"] = model

            target_url = prov_adapter.get_target_url(provider_config_to_dict(prov_config), model)
            if not target_url.startswith("http"):
                target_url = f"http://{target_url}"

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {prov_config.api_key}",
            }

            logger.info(f"[{request_id}] Calling {prov_name} at {target_url}")

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", target_url, json=req_for_provider, headers=headers) as response:
                        response.raise_for_status()

                        converter = None
                        if prov_config.protocol == "chat_openai":
                            converter = OpenAISSEConverter(model)

                        first_chunk_sent = False
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue

                            if not line.startswith("data: "):
                                yield f"{line}\n".encode("utf-8")
                                first_chunk_sent = True
                                continue

                            if converter:
                                raw_chunk = line.encode("utf-8")
                                events = converter.convert_chunk(raw_chunk)
                                for event in events:
                                    yield event
                                    first_chunk_sent = True
                            else:
                                yield f"{line}\n".encode("utf-8")
                                first_chunk_sent = True

                        # 流成功完成（正常结束或客户端断开）
                        if first_chunk_sent:
                            latency_ms = (time.time() - start_time) * 1000
                            logger.info(f"[{request_id}] Streaming completed from {prov_name}, latency={latency_ms/1000:.3f}s")
                            if usage_stats:
                                usage_stats.record(
                                    provider=prov_name, model=model,
                                    input_tokens=0, output_tokens=0,
                                    latency_ms=latency_ms, success=True,
                                    route_rule=matched_rule,
                                )
                            return  # 成功完成，退出

            except httpx.HTTPStatusError as e:
                logger.warning(f"[{request_id}] {prov_name} returned {e.response.status_code}")
                last_error = e
                continue
            except Exception as e:
                # 可能是 "client has been closed" 或其他错误
                logger.warning(f"[{request_id}] {prov_name} streaming error: {e}")
                last_error = e
                continue

        # 所有 provider 都失败
        latency = time.time() - start_time
        logger.error(f"[{request_id}] All streaming providers failed after {latency:.3f}s")
        error_msg = str(last_error) if last_error else "All providers failed"
        error_json = json.dumps({"error": {"type": "upstream_error", "message": error_msg}})
        yield f"data: {error_json}\n\n".encode("utf-8")

        if usage_stats:
            usage_stats.record(
                provider=providers_to_try[0][0] if providers_to_try else "unknown",
                model=providers_to_try[0][1] if providers_to_try else "unknown",
                input_tokens=0, output_tokens=0,
                latency_ms=latency * 1000, success=False,
                route_rule=matched_rule,
            )

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def _handle_streaming(
    request_id: str,
    url: str,
    headers: dict,
    request: dict,
    adapter: ProtocolAdapter,
    provider_protocol: str = "codeplan_anthropic",
    timeout: float = 600.0
) -> StreamingResponse:
    """处理单个流式请求(保留向后兼容)"""

    from .protocol.openai_sse import OpenAISSEConverter

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=request, headers=headers) as response:
                    response.raise_for_status()

                    converter = None
                    if provider_protocol == "chat_openai":
                        model = request.get("model", "")
                        converter = OpenAISSEConverter(model)

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        if not line.startswith("data: "):
                            yield f"{line}\n".encode("utf-8")
                            continue

                        if converter:
                            raw_chunk = line.encode("utf-8")
                            events = converter.convert_chunk(raw_chunk)
                            for event in events:
                                yield event
                        else:
                            yield f"{line}\n".encode("utf-8")

        except Exception as e:
            logger.error(f"[{request_id}] Streaming error: {e}")
            error_json = json.dumps({"error": {"type": "upstream_error", "message": str(e)}})
            yield f"data: {error_json}\n\n".encode("utf-8")

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


def _provider_config_to_dict(provider: ProviderConfig) -> dict:
    """将 ProviderConfig 转换为 dict(供 adapter 使用)"""
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