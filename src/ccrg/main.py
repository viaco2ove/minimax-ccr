"""
CCRG FastAPI 主入口。
"""

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator

import httpx
from httpx import HTTPStatusError
from fastapi import FastAPI, HTTPException, Request, Response
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
_provider_semaphores: dict[str, asyncio.Semaphore] = {}
_provider_last_request_time: dict[str, float] = {}

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
  .header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:20px 32px;border-bottom:1px solid #334155;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
  .header h1{font-size:22px;font-weight:600;color:#f1f5f9}
  .header-right{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .badge{background:#334155;color:#94a3b8;padding:4px 10px;border-radius:12px;font-size:11px}
  .container{max-width:1200px;margin:0 auto;padding:24px}
  .filter-bar{display:flex;align-items:center;gap:8px;margin-bottom:20px;flex-wrap:wrap}
  .filter-btn{background:#1e293b;color:#94a3b8;border:1px solid #334155;padding:7px 16px;border-radius:8px;cursor:pointer;font-size:13px;transition:all .15s}
  .filter-btn:hover{background:#334155;color:#e2e8f0}
  .filter-btn.active{background:#3b82f6;color:#fff;border-color:#3b82f6}
  .filter-sep{width:1px;height:24px;background:#334155;margin:0 4px}
  .custom-range{display:flex;align-items:center;gap:6px;font-size:13px;color:#94a3b8}
  .custom-range input[type="datetime-local"]{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:5px 8px;font-size:12px;outline:none}
  .custom-range input[type="datetime-local"]:focus{border-color:#3b82f6}
  .custom-range button{background:#8b5cf6;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}
  .custom-range button:hover{background:#7c3aed}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px 20px}
  .card .label{font-size:12px;color:#94a3b8;margin-bottom:4px}
  .card .value{font-size:26px;font-weight:700;color:#f1f5f9}
  .card .sub{font-size:11px;color:#64748b;margin-top:4px}
  .section{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin-bottom:24px}
  .section h2{font-size:15px;font-weight:600;margin-bottom:14px;color:#e2e8f9}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 12px;color:#94a3b8;font-weight:500;border-bottom:1px solid #334155;font-size:12px;text-transform:uppercase;letter-spacing:.3px}
  td{padding:10px 12px;border-bottom:1px solid #1e293b}
  tr:hover td{background:#0f172a}
  .tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:500}
  .tag-green{background:#064e3b;color:#6ee7b7}
  .tag-red{background:#450a0a;color:#fca5a5}
  .tag-blue{background:#1e3a5f;color:#93c5fd}
  .bar{height:6px;border-radius:3px;background:#334155;overflow:hidden;margin-top:4px}
  .bar-fill{height:100%;border-radius:3px;transition:width .6s ease}
  .empty{text-align:center;color:#64748b;padding:40px;font-size:14px}
  .range-label{font-size:12px;color:#64748b;margin-bottom:16px}
  #custom-fields{display:none}
  #custom-fields.show{display:flex}
</style>
</head>
<body>
<div class="header">
  <h1>CCRG Dashboard</h1>
  <div class="header-right">
    <span class="badge" id="auto-badge">Auto 30s</span>
  </div>
</div>
<div class="container">
  <div class="filter-bar">
    <button class="filter-btn" data-range="1h" onclick="setRange('1h')">最近1小时</button>
    <button class="filter-btn active" data-range="today" onclick="setRange('today')">今天</button>
    <button class="filter-btn" data-range="month" onclick="setRange('month')">本月</button>
    <button class="filter-btn" data-range="year" onclick="setRange('year')">今年</button>
    <div class="filter-sep"></div>
    <button class="filter-btn" data-range="custom" onclick="setRange('custom')">自定义</button>
    <div id="custom-fields" class="custom-range">
      <input type="datetime-local" id="start-dt">
      <span>~</span>
      <input type="datetime-local" id="end-dt">
      <button onclick="applyCustom()">查询</button>
    </div>
  </div>
  <div class="range-label" id="range-label"></div>
  <div class="cards" id="range-cards"></div>
  <div class="section">
    <h2>各 Provider 用量明细</h2>
    <div id="range-table"></div>
  </div>
  <div class="section">
    <h2>历史累计</h2>
    <div id="summary-table"></div>
  </div>
</div>
<script>
const COLORS=['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981','#ef4444'];
const RANGE_LABELS={"1h":'最近1小时',"today":'今天',"month":'本月',"year":'今年',"custom":'自定义'};
let curRange='today',curStart=null,curEnd=null;

function fmt(n){return n==null?'-':n.toLocaleString()}
function fmtT(n){if(n==null)return'-';if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return n.toString()}
function fmtMs(ms){if(!ms)return'-';return ms<1000?Math.round(ms)+'ms':(ms/1000).toFixed(1)+'s'}
function sTag(s,f){if(s+f===0)return'<span class="tag tag-blue">-</span>';const r=s/(s+f);return r>=0.9?'<span class="tag tag-green">'+s+'/'+(s+f)+'</span>':'<span class="tag tag-red">'+s+'/'+(s+f)+'</span>'}
function barH(pct,c){return'<div class="bar"><div class="bar-fill" style="width:'+Math.min(pct,100)+'%;background:'+c+'"></div></div>'}

function setRange(r){
  curRange=r;
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.toggle('active',b.dataset.range===r));
  const cf=document.getElementById('custom-fields');
  if(r==='custom'){cf.classList.add('show');return}
  cf.classList.remove('show');
  loadStats();
}
function applyCustom(){
  const s=document.getElementById('start-dt').value;
  const e=document.getElementById('end-dt').value;
  if(!s||!e)return;
  curStart=new Date(s).toISOString();
  curEnd=new Date(e).toISOString();
  loadStats();
}
function toLocal(dt){const d=new Date(dt);return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')}

function renderRangeCards(data){
  const el=document.getElementById('range-cards');
  let tReq=0,tIn=0,tOut=0,tTok=0,tOk=0,tFail=0;
  Object.values(data).forEach(d=>{tReq+=d.request_count;tIn+=d.input_tokens;tOut+=d.output_tokens;tTok+=d.total_tokens;tOk+=d.success_count;tFail+=d.fail_count});
  el.innerHTML=`
    <div class="card"><div class="label">请求数</div><div class="value">${fmt(tReq)}</div><div class="sub">${sTag(tOk,tFail)}</div></div>
    <div class="card"><div class="label">输入 Tokens</div><div class="value" style="color:#60a5fa">${fmtT(tIn)}</div></div>
    <div class="card"><div class="label">输出 Tokens</div><div class="value" style="color:#fbbf24">${fmtT(tOut)}</div></div>
    <div class="card"><div class="label">总计 Tokens</div><div class="value" style="color:#c084fc">${fmtT(tTok)}</div><div class="sub">${fmt(tTok)} tokens</div></div>`;
}
function renderRangeTable(data){
  const el=document.getElementById('range-table');
  const entries=Object.entries(data||{});
  if(!entries.length){el.innerHTML='<div class="empty">该时间段暂无数据</div>';return}
  let mx=0;entries.forEach(([_,d])=>{if(d.total_tokens>mx)mx=d.total_tokens});
  let h='<table><tr><th>Provider</th><th>Models</th><th>请求数</th><th>成功次数/总次数</th><th>输入</th><th>输出</th><th>总计 Tokens</th><th>平均延迟</th></tr>';
  entries.forEach(([name,d],i)=>{
    const c=COLORS[i%COLORS.length];
    h+=`<tr><td style="font-weight:600;color:${c}">${name}</td><td style="font-size:11px;color:#94a3b8">${(d.models||[]).join(', ')}</td><td>${fmt(d.request_count)}</td><td>${sTag(d.success_count,d.fail_count)}</td><td>${fmtT(d.input_tokens)}</td><td>${fmtT(d.output_tokens)}</td><td>${fmtT(d.total_tokens)}${barH(mx?d.total_tokens/mx*100:0,c)}</td><td>${fmtMs(d.avg_latency_ms)}</td></tr>`;
  });
  h+='</table>';el.innerHTML=h;
}
function renderSummaryTable(summary){
  const el=document.getElementById('summary-table');
  const entries=Object.entries(summary.providers||{});
  if(!entries.length){el.innerHTML='<div class="empty">暂无历史数据</div>';return}
  let mx=0;entries.forEach(([_,d])=>{if(d.total_tokens>mx)mx=d.total_tokens});
  let h='<table><tr><th>Provider</th><th>总请求数</th><th>总 Tokens</th></tr>';
  entries.forEach(([name,d],i)=>{
    const c=COLORS[i%COLORS.length];
    h+=`<tr><td style="font-weight:600;color:${c}">${name}</td><td>${fmt(d.total_requests)}</td><td>${fmtT(d.total_tokens)}${barH(mx?d.total_tokens/mx*100:0,c)}</td></tr>`;
  });
  h+='</table>';el.innerHTML=h;
}
async function loadStats(){
  let url='/stats?range='+curRange;
  if(curRange==='custom'&&curStart&&curEnd) url+='&start='+encodeURIComponent(curStart)+'&end='+encodeURIComponent(curEnd);
  try{
    const resp=await fetch(url);const data=await resp.json();
    const rl=document.getElementById('range-label');
    rl.textContent=RANGE_LABELS[curRange]||curRange;
    if(curRange==='custom'&&curStart&&curEnd) rl.textContent+=' ('+toLocal(curStart)+' ~ '+toLocal(curEnd)+')';
    renderRangeCards(data.range||{});
    renderRangeTable(data.range||{});
    renderSummaryTable(data.summary||{});
  }catch(e){console.error('Failed:',e)}
}
loadStats();
setInterval(loadStats,30000);
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

    # 初始化 per-provider 并发控制信号量
    global _provider_semaphores
    _provider_semaphores.clear()
    _provider_last_request_time.clear()
    for name, prov in _config.providers.items():
        delay = prov.per_request_delay_ms
        if delay and delay > 0:
            _provider_semaphores[name] = asyncio.Semaphore(1)
            logger.info(f"Rate limit semaphore for {name}: per_request_delay_ms={delay}")

    # 设置日志级别
    log_level_str = _config.server.get("log_level", "info").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.getLogger("ccrg").setLevel(log_level)
    # Also set handler level so DEBUG actually reaches the FileHandler
    _ccrg_logger = logging.getLogger("ccrg")
    if _ccrg_logger.handlers:
        for h in _ccrg_logger.handlers:
            h.setLevel(log_level)
    else:
        # Inherited handlers from root — set their level too
        _root = logging.getLogger()
        for h in _root.handlers:
            h.setLevel(log_level)

    app = FastAPI(title="Claude Code Router Gateway")

    @app.post("/v1/messages")
    async def handle_messages(request: Request):
        return await _handle_request(request)

    @app.get("/health")
    async def health():
        providers = list(_config.providers.keys()) if _config else []
        return {"status": "ok", "providers": providers}

    @app.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request):
        """Count tokens endpoint - not implemented, returns 404 to allow client fallback"""
        return JSONResponse(status_code=404, content={"error": {"type": "not_implemented", "message": "count_tokens is not supported"}})

    @app.get("/stats")
    async def stats(range: str = "today", start: str | None = None, end: str | None = None):
        """Token 使用统计，支持时间范围查询"""
        from datetime import datetime, timedelta

        now = datetime.now()
        range_data = {}

        if range == "custom" and start and end:
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
            range_data = _usage_stats.get_range(s, e) if _usage_stats else {}
        elif range == "1h":
            range_data = _usage_stats.get_range(now - timedelta(hours=1), now) if _usage_stats else {}
        elif range == "today":
            range_data = _usage_stats.get_today() if _usage_stats else {}
        elif range == "month":
            range_data = _usage_stats.get_range(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now) if _usage_stats else {}
        elif range == "year":
            range_data = _usage_stats.get_range(now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), now) if _usage_stats else {}
        else:
            range_data = _usage_stats.get_today() if _usage_stats else {}

        summary = _usage_stats.get_summary() if _usage_stats else {}
        return {
            "range": range_data,
            "summary": summary,
        }

    @app.get("/dashboard")
    async def dashboard():
        """Token 使用可视化面板 — 从独立 HTML 文件加载，修改后刷新即可生效"""
        dashboard_path = Path(__file__).parent / "dashboard.html"
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))

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

    logger.debug(f"[{request_id}] Received request: {json.dumps(body, ensure_ascii=False)[:2000]}")

    # 0. 检查是否启用 workflow
    if _config.workflow.enabled:
        return await _handle_workflow(request, body, request_id)

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
    default_timeout = _config.server.get("timeout_ms", 600000) / 1000

    # 先尝试主 provider，然后是 fallback
    providers_to_try = [(route_result.provider, route_result.model)] + list(route_result.fallback_chain)

    last_error = None
    retried_with_stripped = set()  # 已尝试过剥离重试的 provider
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

            # 获取 provider 对应的超时时间
            prov_timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

            logger.info(f"[{request_id}] Calling {prov_name} at {prov_target_url} (timeout={prov_timeout:.0f}s)")

            if body.get("stream"):
                # 流式请求 - 使用独立函数处理，支持 fallback 重试
                return await _handle_streaming_with_fallback(
                    request_id, body, providers_to_try,
                    _provider_config_to_dict, _registry, _get_adapter_for_provider,
                    _usage_stats, route_result.matched_rule, start_time, default_timeout
                )
            else:
                # 非流式请求
                async with httpx.AsyncClient(timeout=prov_timeout) as client:
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
                error_body = e.response.text[:1000]
            except Exception:
                pass
            logger.error(f"[{request_id}] {prov_name} returned {e.response.status_code}: {error_body}")

            # 400 且错误信息表明模型不支持某功能 → 剥离该功能并重试
            if e.response.status_code == 400 and prov_name not in retried_with_stripped:
                stripped = _strip_unsupported_features(body, error_body)
                if stripped is not body:
                    retried_with_stripped.add(prov_name)
                    logger.info(f"[{request_id}] {prov_name} doesn't support some features, stripping and retrying")
                    # 用剥离后的 body 重试当前 provider
                    try:
                        req_for_provider = prov_adapter.transform_request(stripped, _provider_config_to_dict(prov_config))
                        req_for_provider["model"] = model
                        prov_timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

                        if body.get("stream"):
                            # 流式请求用剥离后的 body 重新走 fallback 链
                            remaining = [(prov_name, model)] + [
                                (p, m) for p, m in providers_to_try
                                if (p, m) != (prov_name, model)
                            ]
                            return await _handle_streaming_with_fallback(
                                request_id, stripped, remaining,
                                _provider_config_to_dict, _registry, _get_adapter_for_provider,
                                _usage_stats, route_result.matched_rule, start_time, default_timeout
                            )
                        else:
                            async with httpx.AsyncClient(timeout=prov_timeout) as client:
                                response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers)
                                response.raise_for_status()
                                resp_data = response.json()
                                transformed_resp = prov_adapter.transform_json_response(resp_data)
                                latency_ms = (time.time() - start_time) * 1000
                                if _usage_stats and isinstance(transformed_resp, dict):
                                    usage = transformed_resp.get("usage", {})
                                    _usage_stats.record(
                                        provider=prov_name, model=model,
                                        input_tokens=usage.get("input_tokens", 0),
                                        output_tokens=usage.get("output_tokens", 0),
                                        latency_ms=latency_ms, success=True,
                                        route_rule=route_result.matched_rule,
                                    )
                                logger.info(f"[{request_id}] Success from {prov_name} (stripped), latency={latency_ms/1000:.3f}s")
                                return JSONResponse(content=transformed_resp)
                    except Exception as retry_e:
                        logger.warning(f"[{request_id}] {prov_name} retry after strip also failed: {retry_e}")

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
    default_timeout: float
) -> StreamingResponse:
    """处理流式请求(带 fallback 支持)"""
    from .protocol.openai_sse import OpenAISSEConverter

    provider_index = [0]  # 用列表包装以便在闭包中修改
    retried_with_stripped = set()  # 已尝试过剥离重试的 provider

    async def stream_generator() -> AsyncGenerator[bytes, None]:
        nonlocal original_body
        last_error = None

        while provider_index[0] < len(providers_to_try):
            prov_name, model = providers_to_try[provider_index[0]]
            provider_index[0] += 1

            prov_config = registry.get(prov_name)
            if not prov_config:
                continue

            # 获取 provider 对应的超时时间
            prov_timeout = (prov_config.timeout_ms / 1000) if prov_config.timeout_ms else default_timeout

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

            logger.info(f"[{request_id}] Calling {prov_name} at {target_url} (timeout={prov_timeout:.0f}s)")

            try:
                async with httpx.AsyncClient(timeout=prov_timeout) as client:
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
                            # 从 converter 中提取 token 使用量
                            if converter and hasattr(converter, "get_usage"):
                                usage = converter.get_usage()
                                input_tokens = usage.get("input_tokens", 0)
                                output_tokens = usage.get("output_tokens", 0)
                            else:
                                input_tokens = 0
                                output_tokens = 0

                            latency_ms = (time.time() - start_time) * 1000
                            logger.info(f"[{request_id}] Streaming completed from {prov_name}, latency={latency_ms/1000:.3f}s")
                            if usage_stats:
                                usage_stats.record(
                                    provider=prov_name, model=model,
                                    input_tokens=input_tokens, output_tokens=output_tokens,
                                    latency_ms=latency_ms, success=True,
                                    route_rule=matched_rule,
                                )
                            return  # 成功完成，退出

            except httpx.HTTPStatusError as e:
                error_body = ""
                try:
                    error_body = e.response.text[:500]
                except Exception:
                    pass
                logger.warning(f"[{request_id}] {prov_name} returned {e.response.status_code}: {error_body}")

                # 400 且错误信息表明模型不支持某功能 → 剥离该功能并重试
                if e.response.status_code == 400 and prov_name not in retried_with_stripped:
                    stripped = _strip_unsupported_features(original_body, error_body)
                    if stripped is not original_body:
                        retried_with_stripped.add(prov_name)
                        logger.info(f"[{request_id}] {prov_name} doesn't support some features, stripping and retrying")
                        # 将当前 provider 重新加入队列
                        providers_to_try.insert(provider_index[0], (prov_name, model))
                        # 替换 original_body 为剥离后的版本
                        original_body = stripped
                        continue

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
        error_json = json.dumps({"type": "error", "error": {"type": "api_error", "message": error_msg}})
        yield f"event: error\ndata: {error_json}\n\n".encode("utf-8")

        if usage_stats:
            usage_stats.record(
                provider=providers_to_try[0][0] if providers_to_try else "unknown",
                model=providers_to_try[0][1] if providers_to_try else "unknown",
                input_tokens=0, output_tokens=0,
                latency_ms=latency * 1000, success=False,
                route_rule=matched_rule,
            )

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


def _strip_unsupported_features(body: dict, error_body: str) -> dict:
    """根据 upstream 400 错误信息，剥离请求中不支持的功能

    检测常见的不支持错误：
    - image_url / image 不支持 -> 剥离图片内容块
    - thinking 不支持 -> 剥离 thinking 字段
    - output_config.effort 无效 -> 修正或删除 output_config
    返回修改后的 body；如果不需要修改则返回原 body。
    """
    changed = False
    result = body

    # 检测图片相关不支持错误
    image_keywords = ["image_url", "image content", "vision", "not supported by certain models"]
    if any(kw.lower() in error_body.lower() for kw in image_keywords):
        result = _strip_image_blocks(result)
        if result is not body:
            changed = True

    # 检测 thinking 相关不支持错误
    thinking_keywords = ["thinking", "extended thinking", "thinking_mode"]
    if any(kw.lower() in error_body.lower() for kw in thinking_keywords):
        if "thinking" in result:
            result = dict(result)
            del result["thinking"]
            changed = True

    # 检测 output_config.effort 相关错误
    effort_keywords = ["output_config.effort", "effort", "xhigh"]
    if any(kw.lower() in error_body.lower() for kw in effort_keywords):
        if "output_config" in result:
            result = dict(result)
            output_config = result["output_config"]
            if isinstance(output_config, dict) and "effort" in output_config:
                # 尝试修正 effort 值
                effort = output_config["effort"]
                valid_efforts = {"low", "medium", "high", "max"}
                if effort not in valid_efforts:
                    result["output_config"] = dict(output_config)
                    if effort == "xhigh":
                        result["output_config"]["effort"] = "high"
                    else:
                        result["output_config"]["effort"] = "medium"
                    changed = True
                else:
                    # 如果值已经是有效的但仍然报错，尝试删除整个 output_config
                    del result["output_config"]
                    changed = True
            else:
                # 如果不是 dict 或没有 effort，删除 output_config
                del result["output_config"]
                changed = True

    return result


def _strip_image_blocks(body: dict) -> dict:
    """从请求中移除所有 image 内容块，替换为 [image] 文本"""
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
                if isinstance(block, dict) and block.get("type") == "image":
                    changed = True
                    new_content.append({"type": "text", "text": "[image]"})
                else:
                    new_content.append(block)
            if changed:
                msg = dict(msg, content=new_content)
        new_messages.append(msg)

    if changed:
        return dict(body, messages=new_messages)
    return body


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
        "timeout_ms": provider.timeout_ms,
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


def _detect_workflow_intent(body: dict, keywords: dict) -> str:
    """基于 keywords.json 检测工作流意图：chat 或 task"""
    workflow_keywords = keywords.get("workflow_intent", {})
    chat_keywords = workflow_keywords.get("chat_intention", [])
    task_keywords = workflow_keywords.get("intention_analyze", [])

    # 收集用户消息文本
    user_texts = []
    for msg in body.get("messages", []):
        role = msg.get("role", "")
        if role != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            user_texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    user_texts.append(block.get("text", ""))

    user_text = " ".join(user_texts).lower()

    # 计算命中
    chat_score = sum(1 for kw in chat_keywords if kw.lower() in user_text)
    task_score = sum(1 for kw in task_keywords if kw.lower() in user_text)

    logger.debug(f"Workflow intent detection: chat={chat_score}, task={task_score}")

    if task_score > chat_score:
        if task_score > 0:
            matched = [kw for kw in task_keywords if kw.lower() in user_text]
            logger.info(f"Matched task keywords: {matched}")
        if chat_score > 0:
            matched = [kw for kw in chat_keywords if kw.lower() in user_text]
            logger.info(f"Matched chat keywords: {matched}")
        return "task"
    
    if chat_score > 0:
        matched = [kw for kw in chat_keywords if kw.lower() in user_text]
        logger.info(f"Matched chat keywords: {matched}")
    return "chat"


def _resolve_execute_route(body: dict, request_id: str) -> str:
    """根据路由规则动态决定 execute_solve 走哪个 provider:model。

    复用标准路由引擎的 scenario/tool_routing/keyword_routing 规则，
    如果路由引擎能匹配到规则，就用路由结果；否则用 workflow 配置的 execute_solve 兜底。
    """
    global _routing_engine, _config

    try:
        tags = _classify_request(body)
        route_result = _routing_engine.route(tags)
        route_str = f"{route_result.provider}:{route_result.model}"
        logger.info(
            f"[{request_id}] execute_solve routed to {route_str} "
            f"via {route_result.matched_rule} ({route_result.matched_reason})"
        )
        return route_str
    except Exception as e:
        logger.warning(f"[{request_id}] execute_solve routing failed ({e}), using workflow default")
        return _config.workflow.get_execute_solve_single()


def _get_stage_routes(stage: str, body: dict = None) -> tuple[list[str], str]:
    """根据 workflow 阶段获取路由列表和步骤名
    
    Args:
        stage: workflow 阶段名称（intention_analyze/chat_intention/analyze_plan/execute_solve）
        body: 原始请求体（用于 scenario 检测）
    
    Returns:
        (route_list, step_name): 路由列表（包含 fallback）和步骤名
    """
    global _config
    
    # 优先检查 scenario：如果有图片，直接走 mmx
    if body:
        tags = _classify_request(body)
        if tags.scenario == "image":
            image_config = _config.routing.get("scenarios", {}).get("image", {})
            route = image_config.get("route", "mmx:MiniMax-M2.7")
            fallback = image_config.get("fallback", [])
            logger.info(f"Image scenario detected, routing to {route}")
            return [route] + fallback, "image"
    
    if stage == "intention_analyze":
        return _config.workflow.get_intention_analyze_list(), "intention_analyze"
    elif stage == "chat_intention":
        return _config.workflow.get_chat_intention_list(), "chat_intention"
    elif stage == "analyze_plan":
        return _config.workflow.get_analyze_plan_list(), "analyze_plan"
    elif stage == "execute_solve":
        return _config.workflow.get_execute_solve_list(), "execute_solve"
    else:
        # 未知阶段，默认 execute_solve
        logger.warning(f"Unknown workflow_stage: {stage}, defaulting to execute_solve")
        return _config.workflow.get_execute_solve_list(), "execute_solve"


def _is_user_initiated_message(body: dict) -> bool:
    """判断请求是否由用户主动输入触发（而非工具调用循环的后续请求）。

    Claude Code 的请求模式：
    - 用户发送新消息：messages 最后一条 user 消息的内容是用户实际输入的文本
    - 工具调用循环：messages 最后一条 user 消息的内容全是 <system-reminder> 或工具结果

    判断标准：最后一条 user 消息中，如果所有文本块都以 <system-reminder> 开头，
    则认为是工具循环回调，不是用户主动输入。
    """
    messages = body.get("messages", [])

    # 找到最后一条 user 消息
    last_user_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg
            break

    if not last_user_msg:
        return False

    content = last_user_msg.get("content", "")

    # 字符串内容：检查是否以 <system-reminder> 开头
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped:
            return False
        return not stripped.startswith("<system-reminder>")

    # 列表内容：检查所有 text 块是否都是 system-reminder
    if isinstance(content, list):
        text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if not text_blocks:
            return False
        # 只要有一个 text 块不是 system-reminder，就认为是用户主动输入
        for block in text_blocks:
            text = block.get("text", "").strip()
            if text and not text.startswith("<system-reminder>"):
                return True
        return False

    return False


def _wrap_non_streaming_response(resp: dict) -> bytes:
    """将非流式 API 响应包装成 Anthropic SSE 流式事件序列。

    Anthropic 流式 API 的事件序列：
    1. message_start (包含 id, type, role, model, content=[], usage)
    2. content_block_start (index, type="text", text=[])
    3. content_block_delta (index, type="text_delta", text=...)
    4. content_block_stop (index)
    5. message_delta (stop_reason, usage)
    6. message_stop
    """
    chunks = []

    msg_id = resp.get("id", f"msg_{uuid.uuid4().hex[:24]}")
    model = resp.get("model", "unknown")
    role = resp.get("role", "assistant")
    stop_reason = resp.get("stop_reason", "end_turn")
    usage = resp.get("usage", {"input_tokens": 0, "output_tokens": 0})

    # 1. message_start
    chunks.append(f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': role, 'model': model, 'content': [], 'stop_reason': None, 'usage': usage}}, ensure_ascii=False)}\n\n")

    # 2-4. content blocks
    content_blocks = resp.get("content", [])
    for idx, block in enumerate(content_blocks):
        if isinstance(block, dict):
            block_type = block.get("type", "text")
            if block_type == "text":
                text = block.get("text", "")
                # content_block_start
                chunks.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': idx, 'content_block': {'type': 'text', 'text': ''}}, ensure_ascii=False)}\n\n")
                # content_block_delta
                chunks.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': idx, 'delta': {'type': 'text_delta', 'text': text}}, ensure_ascii=False)}\n\n")
                # content_block_stop
                chunks.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx}, ensure_ascii=False)}\n\n")
            elif block_type == "tool_use":
                tool_id = block.get("id", f"toolu_{uuid.uuid4().hex[:24]}")
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                # content_block_start
                chunks.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': idx, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': tool_name, 'input': {}}}, ensure_ascii=False)}\n\n")
                # content_block_delta
                input_json = json.dumps(tool_input, ensure_ascii=False)
                chunks.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': idx, 'delta': {'type': 'input_json_delta', 'partial_json': input_json}}, ensure_ascii=False)}\n\n")
                # content_block_stop
                chunks.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx}, ensure_ascii=False)}\n\n")
            elif block_type == "thinking":
                thinking_text = block.get("thinking", "")
                # content_block_start
                chunks.append(f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': idx, 'content_block': {'type': 'thinking', 'thinking': ''}}, ensure_ascii=False)}\n\n")
                # content_block_delta
                chunks.append(f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': idx, 'delta': {'type': 'thinking_delta', 'thinking': thinking_text}}, ensure_ascii=False)}\n\n")
                # content_block_stop
                chunks.append(f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx}, ensure_ascii=False)}\n\n")

    # 5. message_delta
    output_tokens = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0
    chunks.append(f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason}, 'usage': {'output_tokens': output_tokens}}, ensure_ascii=False)}\n\n")

    # 6. message_stop
    chunks.append(f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'}, ensure_ascii=False)}\n\n")

    return "".join(chunks).encode("utf-8")


def _make_streaming_error_sse(error_dict: dict) -> bytes:
    """在流式 generator 中生成一个错误 SSE chunk。

    不能在 streaming generator 里 raise HTTPException（HTTP 200 header 已发送），
    只能 yield 一个包含 error 信息的 SSE chunk，让客户端解析错误。

    Anthropic SDK 要求：
    1. 必须有 `event: error` SSE 行
    2. data 的 JSON 必须有顶层 `"type": "error"`
    3. error 对象格式：`{"type": "<error_type>", "message": "..."}`
    """
    # 从 error_dict 提取 error 信息
    err = error_dict.get("error", {})
    if isinstance(err, dict):
        err_type = err.get("type", "api_error")
        err_msg = err.get("message", str(err))
    else:
        err_type = "api_error"
        err_msg = str(err)

    # 映射内部 error type 到 Anthropic 标准 error type
    type_mapping = {
        "context_length_exceeded": "invalid_request_error",
        "rate_limit_exceeded": "rate_limit_error",
        "workflow_error": "api_error",
        "provider_error": "api_error",
        "upstream_error": "api_error",
        "invalid_request_error": "invalid_request_error",
    }
    anthropic_err_type = type_mapping.get(err_type, "api_error")

    sse_event = "event: error\n"
    sse_data = f"data: {json.dumps({'type': 'error', 'error': {'type': anthropic_err_type, 'message': err_msg}}, ensure_ascii=False)}\n\n"
    return (sse_event + sse_data).encode("utf-8")


def _is_compact_request(body: dict) -> bool:
    """判断请求是否是 /compact 命令（Claude Code 的上下文压缩请求）。

    /compact 请求应该直接透传给模型，不走 analyze_plan/execute_solve 流程。
    """
    messages = body.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            if "/compact" in content:
                return True
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if "/compact" in block.get("text", ""):
                        return True
        break  # 只检查最后一条 user 消息
    return False


def _estimate_messages_tokens(messages: list) -> int:
    """估算 messages 的 token 数（简化实现：字符数 / 4）"""
    total_chars = len(json.dumps(messages, ensure_ascii=False))
    return total_chars // 4


def _truncate_message_content(msg: dict, max_chars: int) -> dict:
    """截断单条消息内部的内容块，保留最后 max_chars 字符的内容。

    用于处理单条消息包含大量 content blocks 的情况（如 /compact 请求
    把所有历史对话塞进一条 user 消息的多个 text blocks 中）。
    """
    content = msg.get("content", "")

    # 字符串内容：直接截断
    if isinstance(content, str):
        if len(content) <= max_chars:
            return msg
        return {**msg, "content": content[:max_chars] + "\n\n[... earlier content truncated ...]"}

    # 列表内容：从后往前保留 blocks
    if isinstance(content, list):
        kept_chars = 0
        kept_blocks = []
        for block in reversed(content):
            block_chars = len(json.dumps(block, ensure_ascii=False))
            if kept_chars + block_chars > max_chars and kept_blocks:
                break
            kept_blocks.append(block)
            kept_chars += block_chars

        kept_blocks.reverse()

        if len(kept_blocks) == len(content):
            return msg

        # 在截断点插入提示 block
        truncation_block = {
            "type": "text",
            "text": "<system-reminder> Earlier conversation history has been truncated to fit context window. The most recent messages are preserved. </system-reminder>"
        }
        return {**msg, "content": [truncation_block] + kept_blocks}

    return msg


def _truncate_messages(messages: list, max_context: int) -> list:
    """兜底截断：保留 system + 最后 N 轮对话，砍掉最早的 messages。

    策略：
    1. 保留所有 system 消息
    2. 保留最后 N 轮对话（估算 token 不超过 max_context 的 70%）
    3. 如果单条消息超限，截断该消息内部的内容块
    4. 在截断点插入一条提示消息说明上下文已被压缩
    """
    char_threshold = int(max_context * 0.7 * 4)  # token → 字符（1 token ≈ 4 chars）

    # 分离 system 消息和对话消息
    system_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    # 从后往前累加，直到超过阈值
    kept_chars = 0
    kept_msgs = []
    for msg in reversed(convo_msgs):
        msg_chars = len(json.dumps(msg, ensure_ascii=False))
        if kept_chars + msg_chars > char_threshold and kept_msgs:
            break
        # 单条消息本身就超限 → 截断消息内部内容
        if msg_chars > char_threshold:
            msg = _truncate_message_content(msg, char_threshold - kept_chars)
        kept_msgs.append(msg)
        kept_chars += len(json.dumps(msg, ensure_ascii=False))

    kept_msgs.reverse()

    # 如果没有截断（全部保留），直接返回原始
    if len(kept_msgs) == len(convo_msgs) and all(
        len(json.dumps(k, ensure_ascii=False)) == len(json.dumps(o, ensure_ascii=False))
        for k, o in zip(kept_msgs, convo_msgs)
    ):
        return messages

    # 在截断点插入提示（如果 kept_msgs 第一条不是截断提示的话）
    truncation_notice = {
        "role": "user",
        "content": "<system-reminder> Earlier conversation history has been truncated to fit context window. The most recent messages are preserved. </system-reminder>"
    }

    result = system_msgs + [truncation_notice] + kept_msgs
    orig_tokens = _estimate_messages_tokens(messages)
    result_tokens = _estimate_messages_tokens(result)
    logger.info(f"Truncated messages: {len(messages)} → {len(result)}, tokens ~{orig_tokens} → ~{result_tokens}")
    return result


async def _handle_workflow(request: Request, body: dict, request_id: str) -> Response:
    """处理 workflow 请求（多步骤 AI 协作）"""
    workflow_config = _config.workflow
    start_time = time.time()
    is_streaming = body.get("stream", False)

    def parse_provider_model(route_str: str) -> tuple[str, str]:
        """解析 provider:model 字符串"""
        if ":" not in route_str:
            return route_str, ""
        return route_str.split(":", 1)

    def build_workflow_message(messages: list, role: str, content: str) -> dict:
        """构建 workflow 消息"""
        return {"role": role, "content": content}

    async def call_provider_with_fallback(
        route_list: list[str], messages: list, step_name: str
    ) -> tuple[dict, bool]:
        """依次尝试每个 provider，返回第一个成功的响应；全部失败则返回最后一个错误"""
        last_resp = None
        for route_str in route_list:
            resp, is_streaming = await call_provider(route_str, messages, step_name)
            # 非 error 即成功
            if not (isinstance(resp, dict) and "error" in resp):
                return resp, is_streaming
            last_resp = resp
            logger.warning(f"[{request_id}] {step_name} provider {route_str} failed, trying next...")
        # 全部失败，返回最后一个错误
        return last_resp or {"error": {"type": "workflow_error", "message": "All providers failed"}}, False

    async def call_provider(route_str: str, messages: list, step_name: str) -> tuple[dict, bool]:
        """调用单个 provider，返回 (response_data, is_streaming)"""
        prov_name, model = parse_provider_model(route_str)
        prov_config = _registry.get(prov_name)
        if not prov_config:
            return {"error": {"type": "provider_error", "message": f"Unknown provider: {prov_name}"}}, False

        prov_adapter = _get_adapter_for_provider(prov_name)

        # 构建请求
        req_body = dict(body)
        req_body["messages"] = messages
        req_body["model"] = model

        req_for_provider = prov_adapter.transform_request(req_body, _provider_config_to_dict(prov_config))
        req_for_provider["model"] = model

        prov_target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
        if not prov_target_url.startswith("http"):
            prov_target_url = f"http://{prov_target_url}"

        prov_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {prov_config.api_key}",
        }

        prov_timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

        # Debug: log request details
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if isinstance(first_user, list):
            first_user = next((c["text"] for c in first_user if c.get("type") == "text"), None)
        preview = str(first_user)[:250].replace("\n", " ") if first_user else "(no user msg)"
        logger.debug(f"[{request_id}] → [{prov_name}] req: model={model}, stream=False, first_user={preview}")
        logger.info(f"[{request_id}] Workflow {step_name}: calling {prov_name} at {prov_target_url}")

        # Rate limit control: delay before request if per_request_delay_ms is configured
        delay = prov_config.per_request_delay_ms
        if delay and delay > 0:
            last_time = _provider_last_request_time.get(prov_name, 0)
            elapsed = time.time() - last_time
            if elapsed < delay / 1000:
                sleep_time = (delay / 1000) - elapsed
                logger.debug(f"[{request_id}] Rate limiting {prov_name}: sleeping {sleep_time:.3f}s (elapsed={elapsed:.3f}s, delay={delay}ms)")
                await asyncio.sleep(sleep_time)
            _provider_last_request_time[prov_name] = time.time()

        try:
            async with httpx.AsyncClient(timeout=prov_timeout) as client:
                response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers)
                response.raise_for_status()
                resp_data = response.json()

                # 检测空响应：HTTP 200 但 content 为空或缺少
                if isinstance(resp_data, dict):
                    content = resp_data.get("content", [])
                    if not content or (isinstance(content, list) and all(
                        not b.get("text", "").strip() for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )):
                        # 空内容 → 视为失败，让 fallback 重试
                        logger.warning(f"[{request_id}] {prov_name} returned 200 but empty content, treating as failed")
                        if _usage_stats:
                            _usage_stats.record(
                                provider=prov_name, model=model,
                                input_tokens=0, output_tokens=0,
                                latency_ms=(time.time() - start_time) * 1000,
                                success=False, route_rule=f"workflow.{step_name}",
                            )
                        return {"error": {"type": "empty_response", "message": f"{prov_name} returned empty content"}}, False

                # 记录 usage
                if _usage_stats and isinstance(resp_data, dict):
                    usage = resp_data.get("usage", {})
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        latency_ms=(time.time() - start_time) * 1000,
                        success=True, route_rule=f"workflow.{step_name}",
                    )

                # Debug: log response preview
                if isinstance(resp_data, dict):
                    content = resp_data.get("content", [])
                    if isinstance(content, list):
                        text_preview = "".join(b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text")[:300]
                        logger.debug(f"[{request_id}] ← [{prov_name}] resp: step={step_name}, preview={text_preview[:200].replace(chr(10),' ')}")
                logger.debug(f"[{request_id}] ← [{prov_name}] completed, step={step_name}")
                return resp_data, False
        except HTTPStatusError as e:
            # 记录详细的错误响应信息
            try:
                error_text = e.response.text
                logger.error(f"[{request_id}] {prov_name} returned {e.response.status_code} error: {error_text[:500]}")
            except Exception as log_err:
                logger.error(f"[{request_id}] Could not read error response: {log_err}")

            # 400 时记录发送的请求体关键字段，帮助诊断 "invalid params"
            if e.response.status_code == 400:
                debug_keys = ["model", "max_tokens", "thinking", "tool_choice", "output_config",
                              "stop_sequences", "temperature", "top_p", "top_k"]
                debug_info = {k: req_for_provider.get(k) for k in debug_keys if k in req_for_provider}
                # tools 只记录数量和名称
                if "tools" in req_for_provider:
                    tools = req_for_provider["tools"]
                    debug_info["tools_count"] = len(tools)
                    debug_info["tool_names"] = [t.get("name","?") for t in tools if isinstance(t, dict)]
                # system 格式
                system = req_for_provider.get("system")
                if system is not None:
                    if isinstance(system, list):
                        debug_info["system_format"] = f"list[{len(system)}]"
                    else:
                        debug_info["system_format"] = f"str[{len(str(system))}]"
                logger.warning(f"[{request_id}] {prov_name} 400 request debug: {json.dumps(debug_info, ensure_ascii=False, default=str)}")

            # 检查是否是 context length 超限错误 (400)
            if e.response.status_code == 400:
                try:
                    err_body = e.response.json()
                    err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
                except Exception:
                    err_msg = str(e)

                # 识别 token 超限相关错误
                token_limit_keywords = [
                    "context length", "token limit", "max tokens",
                    "too many tokens", "exceed", "quota",
                    "length", "limit", "maximum context"
                ]
                is_context_error = any(kw in err_msg.lower() for kw in token_limit_keywords)

                if is_context_error:
                    user_msg = (
                        "API Error: HTTP 400 - Context length limit exceeded. "
                        "Please compress or clear the conversation context."
                    )
                    logger.error(f"[{request_id}] Context length exceeded for {prov_name}: {err_msg}")
                    if _usage_stats:
                        _usage_stats.record(
                            provider=prov_name, model=model,
                            input_tokens=0, output_tokens=0,
                            latency_ms=(time.time() - start_time) * 1000,
                            success=False, route_rule=f"workflow.{step_name}",
                        )
                    return {
                        "error": {
                            "type": "context_length_exceeded",
                            "message": user_msg,
                            "provider_message": err_msg,
                        }
                    }, False

            # 429 Too Many Requests → 视为可 fallback 的错误，返回给 call_provider_with_fallback 重试
            if e.response.status_code == 429:
                logger.warning(f"[{request_id}] Workflow {step_name} hit rate limit (429), returning error for fallback")
                return {"error": {"type": "rate_limit_exceeded", "message": str(e)}}, False

            logger.warning(f"[{request_id}] Workflow {step_name} failed: {e}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=f"workflow.{step_name}",
                )
            return {"error": {"type": "workflow_error", "message": str(e)}}, False
        except Exception as e:
            logger.warning(f"[{request_id}] Workflow {step_name} failed: {e}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=f"workflow.{step_name}",
                )
            return {"error": {"type": "workflow_error", "message": str(e)}}, False

    async def call_provider_streaming(route_str: str, messages: list, step_name: str) -> AsyncGenerator[bytes, None]:
        """流式调用 provider，实时 yield 每个 chunk"""
        prov_name, model = parse_provider_model(route_str)
        prov_config = _registry.get(prov_name)
        if not prov_config:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': f'Unknown provider: {prov_name}'}}, ensure_ascii=False)}\n\n".encode("utf-8")
            return

        prov_adapter = _get_adapter_for_provider(prov_name)

        # 构建请求
        req_body = dict(body)
        req_body["messages"] = messages
        req_body["model"] = model
        req_body["stream"] = True

        req_for_provider = prov_adapter.transform_request(req_body, _provider_config_to_dict(prov_config))
        req_for_provider["model"] = model

        prov_target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
        if not prov_target_url.startswith("http"):
            prov_target_url = f"http://{prov_target_url}"

        prov_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {prov_config.api_key}",
        }

        prov_timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

        # Debug: log the actual request being sent (first user msg + model)
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if isinstance(first_user, list):
            first_user = next((c["text"] for c in first_user if c.get("type") == "text"), None)
        preview = str(first_user)[:250].replace("\n", " ") if first_user else "(no user msg)"
        logger.debug(f"[{request_id}] → [{prov_name}] req: model={model}, stream=True, first_user={preview}")

        logger.info(f"[{request_id}] Workflow {step_name} streaming: {prov_name} at {prov_target_url}")

        # Rate limit control: delay before request if per_request_delay_ms is configured
        delay = prov_config.per_request_delay_ms
        if delay and delay > 0:
            last_time = _provider_last_request_time.get(prov_name, 0)
            elapsed = time.time() - last_time
            if elapsed < delay / 1000:
                sleep_time = (delay / 1000) - elapsed
                logger.debug(f"[{request_id}] Rate limiting {prov_name}: sleeping {sleep_time:.3f}s (elapsed={elapsed:.3f}s, delay={delay}ms)")
                await asyncio.sleep(sleep_time)
            _provider_last_request_time[prov_name] = time.time()

        try:
            async with httpx.AsyncClient(timeout=prov_timeout) as client:
                async with client.stream("POST", prov_target_url, json=req_for_provider, headers=prov_headers) as response:
                    response.raise_for_status()

                    # 根据 provider 类型选择 converter
                    converter = None
                    if prov_config.protocol == "chat_openai":
                        from .protocol.openai_sse import OpenAISSEConverter
                        converter = OpenAISSEConverter(model)
                    elif prov_config.protocol in ("codeplan_anthropic", "mmx", "anthropic"):
                        from .protocol.anthropic_sse import AnthropicSSEConverter
                        converter = AnthropicSSEConverter(model)

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        # 如果是 SSE 格式 data: 开头
                        if line.startswith("data: "):
                            data_content = line[6:]  # 去掉 "data: " 前缀

                            # 如果有 converter，转换格式（converter 会处理 [DONE]）
                            if converter:
                                raw_chunk = line.encode("utf-8")
                                events = converter.convert_chunk(raw_chunk)
                                for event in events:
                                    yield event
                                if data_content == "[DONE]":
                                    break
                            else:
                                # 直接 yield 原始数据
                                if data_content == "[DONE]":
                                    break
                                yield f"{line}\n".encode("utf-8")
                        elif line.startswith("event: ") and converter:
                            # 有 converter 时，event: 行由 converter 内部处理，跳过
                            converter.convert_chunk(line.encode("utf-8"))
                        elif not converter:
                            # 没有 converter 时，直接 yield 原始行
                            yield f"{line}\n".encode("utf-8")

            # 从 converter 中提取 token 使用量
            if converter and hasattr(converter, "get_usage"):
                usage = converter.get_usage()
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
            else:
                input_tokens = 0
                output_tokens = 0

            # 记录成功
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=True, route_rule=f"workflow.{step_name}",
                )
            logger.debug(f"[{request_id}] ← [{prov_name}] stream completed, step={step_name}")

        except HTTPStatusError as e:
            # 记录详细的错误响应信息
            try:
                # 流式响应不能直接 .text，需要先 aread
                await e.response.aread()
                error_text = e.response.text
                logger.error(f"[{request_id}] {prov_name} streaming returned {e.response.status_code} error: {error_text[:500]}")
            except Exception as log_err:
                error_text = ""
                logger.error(f"[{request_id}] Could not read streaming error response: {log_err}")

            # 400 时记录发送的请求体关键字段，帮助诊断 "invalid params"
            if e.response.status_code == 400:
                debug_keys = ["model", "max_tokens", "thinking", "tool_choice", "output_config",
                              "stop_sequences", "temperature", "top_p", "top_k"]
                debug_info = {k: req_for_provider.get(k) for k in debug_keys if k in req_for_provider}
                if "tools" in req_for_provider:
                    tools = req_for_provider["tools"]
                    debug_info["tools_count"] = len(tools)
                    debug_info["tool_names"] = [t.get("name","?") for t in tools if isinstance(t, dict)]
                system = req_for_provider.get("system")
                if system is not None:
                    if isinstance(system, list):
                        debug_info["system_format"] = f"list[{len(system)}]"
                    else:
                        debug_info["system_format"] = f"str[{len(str(system))}]"
                logger.warning(f"[{request_id}] {prov_name} streaming 400 request debug: {json.dumps(debug_info, ensure_ascii=False, default=str)}")

            # 检查是否是 context length 超限错误
            if e.response.status_code == 400:
                try:
                    err_body = e.response.json()
                    err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
                except Exception:
                    err_msg = error_text or str(e)

                token_limit_keywords = [
                    "context length", "token limit", "max tokens",
                    "too many tokens", "exceed", "quota",
                    "length", "limit", "maximum context"
                ]
                is_context_error = any(kw in err_msg.lower() for kw in token_limit_keywords)

                if is_context_error:
                    user_msg = (
                        "API Error: HTTP 400 - Context length limit exceeded. "
                        "Please compress or clear the conversation context."
                    )
                    logger.error(f"[{request_id}] Context length exceeded for {prov_name}: {err_msg}")
                    if _usage_stats:
                        _usage_stats.record(
                            provider=prov_name, model=model,
                            input_tokens=0, output_tokens=0,
                            latency_ms=(time.time() - start_time) * 1000,
                            success=False, route_rule=f"workflow.{step_name}",
                        )
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'invalid_request_error', 'message': user_msg}}, ensure_ascii=False)}\n\n".encode("utf-8")
                    return

                # 非 context 超限的 400 错误（如 invalid params）→ 抛异常让调用方 fallback
                logger.warning(f"[{request_id}] {prov_name} streaming 400 (not context error): {err_msg[:200]}")
                if _usage_stats:
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        success=False, route_rule=f"workflow.{step_name}",
                    )
                raise RuntimeError(f"{prov_name} streaming returned 400: {err_msg[:300]}") from e

            # 429 Too Many Requests → 抛异常让调用方 fallback 到其他 provider
            if e.response.status_code == 429:
                logger.warning(f"[{request_id}] Workflow {step_name} hit rate limit (429) from {prov_name}")
                if _usage_stats:
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        success=False, route_rule=f"workflow.{step_name}",
                    )
                raise RuntimeError(f"{prov_name} streaming returned 429 rate limit") from e

            # 其他 HTTP 错误 → 也抛异常让调用方 fallback
            logger.warning(f"[{request_id}] {prov_name} streaming returned {e.response.status_code}: {error_text[:200]}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=f"workflow.{step_name}",
                )
            raise RuntimeError(f"{prov_name} streaming returned {e.response.status_code}: {error_text[:300]}") from e

        except Exception as e:
            # 非HTTPStatusError 的其他异常 → 也抛出，让调用方 fallback
            logger.warning(f"[{request_id}] {prov_name} streaming failed: {e}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=False, route_rule=f"workflow.{step_name}",
                )
            raise

    # Step 1: Intention Analysis (基于 keywords.json) - 仅用于日志和路由决策
    intent = _detect_workflow_intent(body, _config.keywords)
    is_chat = (intent == "chat")
    is_user_initiated = _is_user_initiated_message(body)
    logger.info(f"[{request_id}] Workflow intention (keyword-based): {intent}, user_initiated={is_user_initiated}")

    # Step 2: CLI 驱动的分步流式交互 - CCR 只做分流+流式透传
    async def workflow_stream_generator() -> AsyncGenerator[bytes, None]:
        # 1. 从 metadata 中获取 workflow_stage
        metadata = body.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
        
        stage = metadata.get("workflow_stage")
        logger.debug(f"metadata workflow_stage {metadata}")

        # 2. 如果没有标注，自动判断（向后兼容）
        if not stage:
            intent = _detect_workflow_intent(body, _config.keywords)
            is_chat = (intent == "chat")
            is_user_initiated = _is_user_initiated_message(body)
            
            if is_chat:
                stage = "chat_intention"
            elif is_user_initiated:
                stage = "intention_analyze"  # 首次用户输入 → 意图分析
            else:
                stage = "execute_solve"      # 工具回调 → 直接执行
            
            logger.info(f"[{request_id}] Workflow stage (auto-detected): {stage}")
        else:
            logger.info(f"[{request_id}] Workflow stage (from metadata): {stage}")

        # 3. 根据阶段选择路由和 fallback
        route_list, step_name = _get_stage_routes(stage, body)

        msgs = body.get("messages", [])

        # /compact 请求特殊处理
        is_compact = _is_compact_request(body)
        if is_compact:
            logger.info(f"[{request_id}] /compact request → direct pass-through, overriding stage to execute_solve")
            route_list, step_name = _get_stage_routes("execute_solve", body)

        # 预检 context window - 80% 阈值自动截断
        first_route = route_list[0] if route_list else "minimax:MiniMax-M2.7"
        prov_name, _ = first_route.split(":", 1) if ":" in first_route else (first_route, "")
        prov_config = _registry.get(prov_name)
        max_context = prov_config.capabilities.get("max_context", 128000) if prov_config else 128000
        estimated_tokens = _estimate_messages_tokens(msgs)

        if estimated_tokens > max_context * 0.8:
            msgs = _truncate_messages(msgs, max_context)
            logger.info(
                f"[{request_id}] Pre-emptive truncation: estimated {estimated_tokens} tokens > {max_context * 0.8:.0f} (80% of {max_context}), "
                f"truncating {len(body.get('messages', []))} → {len(msgs)} messages"
            )

        # 4. 流式调用 provider（带 fallback）
        success = False
        last_error = None
        for try_route in route_list:
            try:
                logger.info(f"[{request_id}] Trying {try_route} for {step_name}")
                async for chunk in call_provider_streaming(try_route, msgs, step_name):
                    yield chunk
                    success = True  # 至少成功 yield 了一个 chunk
                if success:
                    logger.info(f"[{request_id}] {step_name} succeeded with {try_route}")
                    break
            except Exception as e:
                last_error = e
                logger.warning(f"[{request_id}] {try_route} failed for {step_name}: {e}, trying next...")
                success = False
                continue

        if not success:
            error_msg = str(last_error) if last_error else f"All {len(route_list)} providers failed for {step_name}"
            logger.error(f"[{request_id}] {error_msg}")
            yield _make_streaming_error_sse({"error": {"type": "provider_error", "message": error_msg}})

    return StreamingResponse(workflow_stream_generator(), media_type="text/event-stream")


def run(host: str | None = None, port: int | None = None, config_path: str | None = None):
    """启动 Gateway"""
    import uvicorn

    init_app(config_path)

    host = host or (_config.server.get("host", "127.0.0.1") if _config else "127.0.0.1")
    port = port or (_config.server.get("port", 3458) if _config else 3458)

    logger.info(f"Starting CCRG on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    run()