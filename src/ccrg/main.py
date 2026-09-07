"""
CCRG FastAPI 主入口。
"""

import asyncio
import contextlib
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
from .protocol import AnthropicAdapter, OpenAIAdapter, MiniMaxAdapter, ProtocolAdapter
from .translator.openai_translator import convert_chunks_to_json
from .translator.sse_client import _stream_wrapper, collect_request
from .classifier.scenario import ScenarioClassifier
from .classifier.tool_type import ToolTypeClassifier
from .classifier.keyword import KeywordClassifier
from .provider.registry import ProviderRegistry
from .router import RoutingEngine
from .router.fallback import FallbackRouter
from .splitter.workflow import WorkflowSplitter
from .splitter import Splitter, SplitterFactory
from .splitter.base import RoutingDecision
from .types import GatewayConfig, ProviderConfig, RequestTags
from .usage_stats import get_usage_stats

# 配置日志
import os
from pathlib import Path

log_file = Path("logs/ccrg.log")
log_file.parent.mkdir(parents=True, exist_ok=True)

# 按天分割日志：每天午夜滚动，保留 14 天历史。
# 分割后历史文件形如 ccrg.log.2026-07-21，当天日志始终写入 ccrg.log。
from logging.handlers import TimedRotatingFileHandler

_file_handler = TimedRotatingFileHandler(
    log_file,
    when="midnight",
    interval=1,
    backupCount=14,
    encoding="utf-8",
    utc=False,
)
_file_handler.suffix = "%Y-%m-%d"  # 历史文件后缀格式

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        _file_handler,
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
_workflow_splitter: Splitter | None = None
_workflow_stage_splitter: Splitter | None = None
_provider_semaphores: dict[str, asyncio.Semaphore] = {}
_provider_last_request_time: dict[str, float] = {}

# ── 并发控制 & 连接池 ─────────────────────────────────────────────
# 全局并发上限：同时处理的请求数（含流式）。4 客户端 × 多轮工具调用时，
# 超过此数的请求排队等待，避免大请求同时在内存里导致 OOM。
_global_concurrency: asyncio.Semaphore | None = None
# 全局共享 httpx 连接池：避免每请求新建 client 的连接/TLS 开销与文件描述符压力。
_http_client: httpx.AsyncClient | None = None

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
    global _config, _registry, _routing_engine, _usage_stats, app, _workflow_splitter, _workflow_stage_splitter

    _config = load_config(config_path)
    _registry = ProviderRegistry(_config)
    _routing_engine = RoutingEngine(_config)
    _usage_stats = get_usage_stats(_config)

    # 创建 splitter（根据配置选择 active_strategy）
    splitter_cfg = _config.routing.get("splitter", {})
    active_strategy = splitter_cfg.get("active_strategy", "keyword_splitter")
    _workflow_splitter = SplitterFactory.create(
        active_strategy=active_strategy,
        config=_config.__dict__ if hasattr(_config, "__dict__") else {},
        keywords=_config.keywords,
        registry=_registry,
        usage_stats=_usage_stats,
    )

    # 预加载 semantic_splitter 模型（避免第一次请求时才下载）
    if active_strategy == "semantic_splitter":
        logger.info("[SemanticSplitterLocal] 预加载模型中(2.0)...")
        try:
            _workflow_splitter._load_model()
        except Exception as e:
            logger.error(f"[SemanticSplitterLocal] 模型预加载失败: {e}（语义分块将降级为关键词路由，首请求也会按需重试加载）")
        else:
            if getattr(_workflow_splitter, "_model", None) is None:
                logger.warning(
                    "[SemanticSplitterLocal] 预加载未实际加载模型（可能 sentence_transformers 缺失），"
                    "语义分块将降级为关键词路由"
                )
            else:
                logger.info("[SemanticSplitterLocal] 模型预加载完成")

    # 创建独立的 workflow 阶段 splitter（与共享 _workflow_splitter 分开）
    # 依据 workflow.workflow_splitter 配置（enabled/active_strategy/semantic_splitter/llm_splitter）
    # 判断当前属于哪个 workflow 阶段；未启用时保持 None（向后兼容旧逻辑）。
    _workflow_stage_splitter = None
    workflow_splitter_cfg = _config.workflow.get_workflow_splitter_config()
    if _config.workflow.is_workflow_splitter_enabled() and workflow_splitter_cfg:
        wf_active_strategy = workflow_splitter_cfg.get("active_strategy", "keyword_splitter")
        # 构造浅拷贝 config 字典（routing / providers 等其余内容保留），
        # 由 WorkflowSplitter 内部从 workflow.workflow_splitter 读取自身配置，
        # 不再注入 config["routing"]["splitter"]，不复用下级 splitter 代码
        wf_config = dict(_config.__dict__) if hasattr(_config, "__dict__") else {}
        wf_config["routing"] = dict(_config.routing)
        try:
            _workflow_stage_splitter = WorkflowSplitter(
                config=wf_config,
                keywords=_config.keywords,
                registry=_registry,
                usage_stats=_usage_stats,
            )
            # 预加载 semantic 模型（避免第一次请求时才下载）
            if wf_active_strategy == "semantic_splitter":
                logger.info("[WorkflowStageSplitter] 预加载 semantic 模型中...")
                try:
                    _workflow_stage_splitter._load_model()
                except Exception as e:
                    logger.error(f"[WorkflowStageSplitter] 模型预加载失败: {e}（语义分块将降级为关键词路由，首请求也会按需重试加载）")
                else:
                    if getattr(_workflow_stage_splitter, "_model", None) is None:
                        logger.warning(
                            "[WorkflowStageSplitter] 预加载未实际加载模型（可能 sentence_transformers 缺失），"
                            "语义分块将降级为关键词路由"
                        )
                    else:
                        logger.info("[WorkflowStageSplitter] 模型预加载完成")
            logger.info(f"[WorkflowStageSplitter] created: active_strategy={wf_active_strategy}")
        except Exception as e:
            logger.error(f"[WorkflowStageSplitter] 创建失败: {e}，将回退到 _infer_stage_from_context")
            _workflow_stage_splitter = None
    else:
        logger.info("[WorkflowStageSplitter] workflow_splitter 未启用，独立阶段判定回退到上下文推断")

    # 初始化 per-provider 并发控制信号量
    global _provider_semaphores
    _provider_semaphores.clear()
    _provider_last_request_time.clear()
    for name, prov in _config.providers.items():
        delay = prov.per_request_delay_ms
        if delay and delay > 0:
            _provider_semaphores[name] = asyncio.Semaphore(1)
            logger.info(f"Rate limit semaphore for {name}: per_request_delay_ms={delay}")

    # 初始化全局并发控制（防止内存爆炸）
    global _global_concurrency, _http_client
    _global_concurrency = asyncio.Semaphore(8)
    logger.info("Global concurrency semaphore: max 8 concurrent requests")

    # 初始化全局 httpx 客户端（共享连接池，避免每请求新建 TLS 连接）
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=30.0),
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    )
    logger.info("Global httpx client initialized: max_connections=32, max_keepalive=16")

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

    # 确保 translator 子模块的 logger 也能输出 DEBUG
    _translator_logger = logging.getLogger("ccrg.translator")
    _translator_logger.setLevel(log_level)


    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # ========== 启动时执行（替代 @app.on_event("startup")） ==========
        global _http_client
        # 初始化你日志里的连接池参数
        limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
        _http_client = httpx.AsyncClient(limits=limits, timeout=600)
        logger.info(f"Global httpx client initialized: max_connections=32, max_keepalive=16")

        yield  # 此处分割，服务运行中

        # ========== 关闭时执行（替代 @app.on_event("shutdown")） ==========
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None
            logger.info("Global httpx client closed")

    # 实例化 FastAPI 绑定生命周期
    app = FastAPI(lifespan=lifespan)

    @app.post("/v1/messages")
    async def handle_messages(request: Request):
        return await _handle_request(request)


    @app.post("/v1/responses")
    async def handle_responses(request: Request):
        """Anthropic Responses API 端点（与 /v1/messages 等价）"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request", "message": "Invalid JSON"}}
            )

        # /v1/responses 请求结构：body.input.messages
        # 转换为 CCRG 内部格式：body.messages（与 /v1/messages 相同）
        if "input" in body and isinstance(body["input"], dict):
            body["messages"] = body.pop("input").get("messages", [])

        logger.debug(f"[RESPONSES] model={body.get('model')}, stream={body.get('stream')}")

        # 复用 /v1/messages 的处理逻辑
        # 构造 FakeRequest 避免重复读取 body 流
        class FakeRequest:
            def __init__(self, json_body):
                self._json = json_body

            async def json(self):
                return self._json

        fake = FakeRequest(body)
        return await _handle_request(fake)

    @app.post("/v1/chat/completions")
    async def handle_chat_completions(request: Request):
        """OpenAI Chat Completions 格式端点 - 转换为 Anthropic 格式后处理"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": {"type": "invalid_request", "message": "Invalid JSON"}}
            )

        logger.debug(f"[TRANSLATOR_OPENAI] INPUT: {json.dumps(body, ensure_ascii=False)[:500]}")

        # OpenAI 格式 -> Anthropic 格式
        transformed_body = _convert_openai_to_anthropic(body)
        logger.debug(f"[TRANSLATOR_OPENAI] TRANSFORMED: {json.dumps(transformed_body, ensure_ascii=False)[:500]}")

        logger.debug(f"[TRANSLATOR_OPENAI] stream=false, forcing stream=True for chunk collection")

        if transformed_body.get("stream"):
            # stream=true: 实时 yield SSE chunks
            return StreamingResponse(
                _stream_wrapper(_handle_request, transformed_body),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"}
            )
        else:
            # stream=false: 强制 stream=True 以收集 chunks，再转换为 JSON
            transformed_body = dict(transformed_body)
            transformed_body["stream"] = True
            chunks, model = await collect_request(_handle_request, transformed_body)
            resp_data = convert_chunks_to_json(chunks, model)
            logger.debug(f"[TRANSLATOR_OPENAI] resp_data:{resp_data}")

            return JSONResponse(content=resp_data)

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

    # ── MCP Server 端点（与 CCRG 共用端口）─────────────────────────

    from .mcp_server.server import register_routes
    register_routes(app, lambda: f"http://127.0.0.1:{_config.server.get('port', 3428)}" if _config else "http://127.0.0.1:3428")

    return app


def _get_auth_header_for_provider(prov_config) -> tuple[str, str]:
    """获取 provider 的 auth header 名称和值

    Returns:
        (header_name, header_value) 元组
    """
    providers_adapter = getattr(prov_config, 'providers_adapter', None) or getattr(prov_config, 'provider_adapter', None)
    if providers_adapter == "xiaomi":
        return ("api-key", prov_config.api_key)
    else:
        return ("Authorization", f"Bearer {prov_config.api_key}")


def _make_curl_cmd(target_url: str, req_file_path: str, prov_config) -> str:
    """生成 curl 命令字符串（密钥脱敏，保留 Bearer 等前缀）"""
    auth_name, auth_value = _get_auth_header_for_provider(prov_config)
    # 脱敏密钥部分，保留前缀（如 "Bearer "）
    if " " in auth_value:
        prefix, key_part = auth_value.rsplit(" ", 1)
        prefix = prefix + " "
    else:
        prefix, key_part = "", auth_value
    if len(key_part) > 8:
        masked = f"{key_part[:4]}...{key_part[-4:]}"
    else:
        masked = "****"
    auth_display = f"{prefix}{masked}"
    return (
        f"curl -X POST '{target_url}' \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -H '{auth_name}: {auth_display}' \\\n"
        f"  -H 'anthropic-version: 2023-06-01' \\\n"
        f"  -d @{req_file_path}"
    )


def _build_provider_headers(prov_config) -> dict:
    """构建 provider 的 HTTP headers"""
    headers = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    auth_name, auth_value = _get_auth_header_for_provider(prov_config)
    headers[auth_name] = auth_value
    return headers


def _wrap_with_concurrency(gen_factory, request_id: str):
    """用全局并发信号量包装一个流式 generator factory。

    gen_factory: 无参 callable，返回 async generator。
    限制同时处理的流式请求数，防止大量大请求同时在内存里导致 OOM。
    """
    async def _wrapped():
        if _global_concurrency is not None:
            await _global_concurrency.acquire()
            logger.debug(f"[{request_id}] acquired global concurrency slot")
        try:
            async for chunk in gen_factory():
                yield chunk
        finally:
            if _global_concurrency is not None:
                _global_concurrency.release()
                logger.debug(f"[{request_id}] released global concurrency slot")
    return _wrapped()


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

    msgs = body.get("messages", [])
    logger.debug(f"[{request_id}] Received request: model={body.get('model')}, messages={len(msgs)}, stream={body.get('stream')}")

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

            prov_headers = _build_provider_headers(prov_config)

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
                async with contextlib.nullcontext(_http_client) as client:
                    response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers, timeout=prov_timeout)
                    response.raise_for_status()

                    resp_data = response.json()
                    # 检查空响应
                    if not resp_data:
                        raise ValueError(f"{prov_name} returned empty/null response")

                    transformed_resp = prov_adapter.transform_json_response(resp_data)
                    if not transformed_resp:
                        raise ValueError(f"{prov_name} transform returned empty/null response")

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
                            success=1,
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
                stripped = _strip_unsupported_features(error_body, body, prov_config.protocol)
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
                            # Debug: 保存剥离后的请求体到文件
                            if logger.isEnabledFor(logging.DEBUG):
                                req_dir = Path("logs/req")
                                req_dir.mkdir(parents=True, exist_ok=True)
                                req_file = req_dir / f"{request_id}_{prov_name}_strip.json"
                                with open(req_file, "w", encoding="utf-8") as f:
                                    json.dump(req_for_provider, f, ensure_ascii=False)
                                curl_cmd = _make_curl_cmd(prov_target_url, f"logs/req/{req_file.name}", prov_config)
                                logger.debug(f"[FallbackRouter] [REQ] [CURL]1 [{prov_name}] [{model}]: {req_file} (chars={len(json.dumps(req_for_provider, ensure_ascii=False))})\n{curl_cmd}")

                            async with contextlib.nullcontext(_http_client) as client:
                                response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers, timeout=prov_timeout)
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
                                        latency_ms=latency_ms, success=1,
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
                    success=0, route_rule=route_result.matched_rule,
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
                    success=0, route_rule=route_result.matched_rule,
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

            headers = _build_provider_headers(prov_config)

            logger.info(f"[{request_id}] Calling {prov_name} at {target_url} (timeout={prov_timeout:.0f}s)")

            try:
                async with contextlib.nullcontext(_http_client) as client:
                    async with client.stream("POST", target_url, json=req_for_provider, headers=headers, timeout=prov_timeout) as response:
                        response.raise_for_status()

                        converter = None
                        if prov_config.protocol == "chat_openai":
                            converter = OpenAISSEConverter(model)

                        # 用于 Anthropic SSE 事件解析 usage
                        usage_input_tokens = 0
                        usage_output_tokens = 0

                        first_chunk_sent = False
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue

                            if not line.startswith("data: "):
                                yield f"{line}\n".encode("utf-8")
                                first_chunk_sent = True
                                continue

                            # 解析 Anthropic SSE 事件获取 usage
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str.startswith("{"):
                                    try:
                                        import json as _json
                                        event_data = _json.loads(data_str)
                                        event_type = event_data.get("type", "")
                                        if event_type == "message_start":
                                            msg = event_data.get("message", {})
                                            usage = msg.get("usage", {})
                                            if not usage_input_tokens:
                                                usage_input_tokens = usage.get("input_tokens", 0)
                                        elif event_type == "message_delta":
                                            usage = event_data.get("usage", {})
                                            usage_output_tokens = usage.get("output_tokens", 0)
                                    except Exception:
                                        pass

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
                            # 从 converter 或 SSE 事件中提取 token 使用量
                            if converter and hasattr(converter, "get_usage"):
                                usage = converter.get_usage()
                                input_tokens = usage.get("input_tokens", 0)
                                output_tokens = usage.get("output_tokens", 0)
                            else:
                                input_tokens = usage_input_tokens
                                output_tokens = usage_output_tokens

                            latency_ms = (time.time() - start_time) * 1000
                            logger.info(f"[{request_id}] Streaming completed from {prov_name}, latency={latency_ms/1000:.3f}s")
                            if usage_stats:
                                usage_stats.record(
                                    provider=prov_name, model=model,
                                    input_tokens=input_tokens, output_tokens=output_tokens,
                                    latency_ms=latency_ms, success=1,
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
                    stripped = _strip_unsupported_features(error_body, original_body, prov_config.protocol)
                    if stripped is not original_body:
                        retried_with_stripped.add(prov_name)
                        logger.info(f"[{request_id}] {prov_name} doesn't support some features, stripping and retrying")
                        # Debug: 保存剥离后的请求体到文件
                        if logger.isEnabledFor(logging.DEBUG):
                            req_dir = Path("logs/req")
                            req_dir.mkdir(parents=True, exist_ok=True)
                            req_file = req_dir / f"{request_id}_{prov_name}_strip.json"
                            with open(req_file, "w", encoding="utf-8") as f:
                                json.dump(stripped, f, ensure_ascii=False)
                            curl_cmd = _make_curl_cmd(target_url, f"logs/req/{req_file.name}", prov_config)
                            logger.debug(f"[FallbackRouter] [REQ] [CURL]2 [{prov_name}]: {req_file} (chars={len(json.dumps(stripped, ensure_ascii=False))})\n{curl_cmd}")
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
                latency_ms=latency * 1000, success=0,
                route_rule=matched_rule,
            )

    return StreamingResponse(
        _wrap_with_concurrency(stream_generator, request_id),
        media_type="text/event-stream",
    )


def _strip_unsupported_features(error_msg: str, req_for_provider: dict, protocol: str = "") -> dict:
    """根据 upstream 400 错误信息和当前请求，剥离可能导致 400 的功能

    直接基于请求字段进行剥离，适用于 call_provider_streaming 的重试逻辑。
    对于 codeplan_anthropic 协议，额外清理可能导致 400 的字段：
    - thinking 字段可能导致 "thinking not supported" 错误
    - output_config 可能导致 "output_config.effort" 相关错误
    - tool_choice 的 any/tool 类型可能不被支持

    protocol 参数应传 provider 配置里的真实 protocol（如 "codeplan_anthropic"），
    注意：请求体本身不含 protocol 字段，所以不能从 req_for_provider 里取。
    """
    result = dict(req_for_provider)

    # 优先根据错误信息进行针对性剥离
    error_lower = error_msg.lower()

    # 检测图片相关不支持错误
    image_keywords = ["image_url", "image content", "vision", "not supported by certain models", "image block"]
    if any(kw in error_lower for kw in image_keywords):
        result = _strip_image_blocks(result)

    # 检测 thinking 相关不支持错误
    thinking_keywords = ["thinking", "extended thinking", "thinking_mode", "not support thinking"]
    if any(kw in error_lower for kw in thinking_keywords):
        if "thinking" in result:
            result = dict(result)
            del result["thinking"]

    # 检测 output_config.effort 相关错误
    effort_keywords = ["output_config.effort", "effort", "xhigh"]
    if any(kw in error_lower for kw in effort_keywords):
        if "output_config" in result:
            result = dict(result)
            if isinstance(result["output_config"], dict) and "effort" in result["output_config"]:
                effort = result["output_config"]["effort"]
                valid_efforts = {"low", "medium", "high", "max"}
                if effort not in valid_efforts:
                    result["output_config"] = dict(result["output_config"])
                    if effort == "xhigh":
                        result["output_config"]["effort"] = "high"
                    else:
                        result["output_config"]["effort"] = "medium"
                else:
                    del result["output_config"]
            else:
                del result["output_config"]

    # 对于 codeplan_anthropic 协议，额外做预防性剥离
    # 因为 400 错误可能是由 provider 不支持的 Claude Code 特有参数导致的
    # 注意：protocol 来自 provider 配置，不能从请求体里取（请求体没有这个字段）
    if protocol in ("codeplan_anthropic", "mmx"):
        # 预防性剥离 thinking（MiniMax 等 provider 的 codeplan 接口可能不支持）
        if "thinking" in result:
            result = dict(result)
            del result["thinking"]

        # 清理 tool_choice
        if "tool_choice" in result:
            tc = result["tool_choice"]
            if isinstance(tc, dict):
                tc_type = tc.get("type", "")
                if tc_type in ("any", "tool"):
                    result = dict(result)
                    result["tool_choice"] = {"type": "auto"}

        # 确保 max_tokens 不超过 32K？ 降低一点 30000 或者保守一点 22000
        # 输入总 token + max_tokens ≤ 模型上下文窗口上限
        if "max_tokens" in result:
            try:
                current_max = int(result["max_tokens"])
                logger.debug(f"Routed to main 客户端 max_tokens : {result["max_tokens"]}")
                if current_max > 22000:
                    result = dict(result)
                    result["max_tokens"] = 22000
            except (ValueError, TypeError):
                pass

    # 清理 messages 中的空 text blocks 和 thinking blocks（预防性剥离）
    result = _strip_empty_and_unsupported_blocks(result)

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


def _strip_empty_and_unsupported_blocks(body: dict) -> dict:
    """清理消息中的空 text blocks 和 thinking blocks"""
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
                    if text and text.strip():
                        new_content.append(block)
                    else:
                        changed = True
                    continue
                # 跳过 thinking block（MiniMax 等不支持）
                if block_type == "thinking":
                    changed = True
                    continue
                new_content.append(block)
            if changed:
                msg = dict(msg, content=new_content)
        new_messages.append(msg)

    if changed:
        return dict(body, messages=new_messages)
    return body


def _pick_vision_route() -> str | None:
    """从 routing.scenarios.image 的 route+fallback 里取第一个 vision:true 的 provider:model。

    不修改 routing 配置，仅用于 image 预处理时选择一个能看图的 provider。
    """
    global _config, _registry
    if not _config:
        return None
    image_cfg = _config.routing.get("scenarios", {}).get("image", {})
    candidates = []
    if image_cfg.get("route"):
        candidates.append(image_cfg["route"])
    candidates.extend(image_cfg.get("fallback", []))
    for route in candidates:
        if ":" not in route:
            continue
        prov_name, _ = route.split(":", 1)
        prov = _registry.get(prov_name)
        if prov and prov.capabilities.get("vision", False):
            return route
    return None


def _collect_image_blocks(messages: list) -> list[tuple[int, int, dict]]:
    """收集所有 image 内容块的位置。

    返回 [(msg_index, block_index, image_block), ...]，遍历全部消息（含历史）。
    支持 Anthropic 原生格式 {"type":"image","source":{...}}。
    """
    result = []
    for mi, msg in enumerate(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image":
                result.append((mi, bi, block))
    return result


async def _describe_image_block(image_block: dict, vision_route: str, request_id: str) -> str:
    """调用 vision provider 把单个 image 块转成文字描述。

    失败时返回 "[image]" 占位（与原 strip 行为一致），不阻断主流程。
    """
    global _http_client, _registry
    prov_name, model = vision_route.split(":", 1)
    prov_config = _registry.get(prov_name)
    if not prov_config:
        logger.warning(f"[{request_id}] image preprocess: vision provider {prov_name} not found")
        return "[image]"

    # 构建一个只含该图片 + 描述指令的最小请求
    describe_req = {
        "model": model,
        "max_tokens": 1024,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                image_block,
                {"type": "text", "text": "请用中文简洁客观地描述这张图片的内容，包括可见的文字、UI 元素、图表数据等关键信息。不要发表观点，只描述你看到的。"},
            ],
        }],
    }
    prov_adapter = _get_adapter_for_provider(prov_name)
    req_for_provider = prov_adapter.transform_request(describe_req, _provider_config_to_dict(prov_config))
    req_for_provider["model"] = model
    target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
    if not target_url.startswith("http"):
        target_url = f"http://{target_url}"
    headers = _build_provider_headers(prov_config)
    timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

    try:
        async with contextlib.nullcontext(_http_client) as client:
            resp = await client.post(target_url, json=req_for_provider, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            # 提取 text 内容
            content = data.get("content", [])
            texts = []
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
            desc = "".join(texts).strip()
            if not desc:
                logger.warning(f"[{request_id}] image preprocess: vision returned empty, using [image]")
                return "[image]"
            logger.info(f"[{request_id}] image preprocess: described image via {vision_route} ({len(desc)} chars)")
            return f"[图片内容：{desc}]"
    except Exception as e:
        logger.warning(f"[{request_id}] image preprocess: vision call failed ({e}), using [image]")
        return "[image]"


async def _preprocess_images_for_provider(messages: list, prov_config, request_id: str) -> list:
    """若目标 provider 不支持 vision 且 messages 含图片，先把图片转成文字描述。

    返回处理后的 messages（若无图或 provider 支持 vision，原样返回）。
    在 transform_request 之前调用，保证 vision:false 的 provider（如 glm-5.2）
    收到的是图片文字描述而非裸 image 块，避免 400 "Model only support text input"。
    """
    # provider 支持 vision -> 无需预处理
    caps = prov_config.capabilities if prov_config else {}
    if caps.get("vision", False):
        return messages

    image_locs = _collect_image_blocks(messages)
    if not image_locs:
        return messages

    vision_route = _pick_vision_route()
    if not vision_route:
        logger.info(f"[{request_id}] image preprocess: no vision provider available, leaving {len(image_locs)} images as-is")
        return messages

    logger.info(f"[{request_id}] image preprocess: {len(image_locs)} image(s) -> vision route {vision_route}")
    # 逐个描述（串行，避免并发触发 rate limit）
    # 用浅拷贝 + 原地替换 block，保持 messages 结构
    new_messages = [dict(m) for m in messages]
    for mi, msg in enumerate(new_messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_content = list(content)
        changed = False
        for bi, block in enumerate(new_content):
            if isinstance(block, dict) and block.get("type") == "image":
                desc = await _describe_image_block(block, vision_route, request_id)
                new_content[bi] = {"type": "text", "text": desc}
                changed = True
        if changed:
            msg["content"] = new_content
    return new_messages


def _provider_config_to_dict(provider: ProviderConfig) -> dict:
    """将 ProviderConfig 转换为 dict(供 adapter 使用)"""
    return {
        "name": provider.name,
        "api_base_url": provider.api_base_url,
        "api_key": provider.api_key,
        "protocol": provider.protocol,
        "providers_adapter": provider.providers_adapter,
        "models": provider.models,
        "capabilities": provider.capabilities,
        "cost_tier": provider.cost_tier,
        "default_params": provider.default_params,
        "retry": provider.retry,
        "timeout_ms": provider.timeout_ms,
    }


def _get_adapter_for_provider(provider_name: str) -> ProtocolAdapter:
    """获取 provider 对应的 adapter

    优先使用 provider config 中的 providers_adapter 字段，
    其次根据 protocol 字段选择默认 adapter。
    """
    provider = _registry.get(provider_name)
    if not provider:
        return AnthropicAdapter()

    # 优先使用 providers_adapter 配置
    adapter_name = provider.providers_adapter
    if adapter_name == "minimax":
        return MiniMaxAdapter()
    elif adapter_name == "openai":
        return OpenAIAdapter()
    elif adapter_name == "anthropic":
        return AnthropicAdapter()

    # 回退到 protocol 字段
    if provider.protocol == "codeplan_anthropic":
        return AnthropicAdapter()
    elif provider.protocol == "chat_openai":
        return OpenAIAdapter()
    elif provider.protocol == "mmx":
        return AnthropicAdapter()
    else:
        return AnthropicAdapter()


def _classify_request(request: dict, stage: str = None) -> RequestTags:
    """对请求进行分类"""
    global _config, _classifier_scenario, _classifier_tool

    # Scenario 分类
    config_dict = _config.__dict__ if hasattr(_config, "__dict__") else {"routing": getattr(_config, "routing", {})}
    tags = _classifier_scenario.extract_tags(request, config_dict)

    # Tool 类型分类（intention_analyze 阶段跳过，避免 tool_routing 劫持）
    if stage != "intention_analyze":
        tool_types, tool_details = _classifier_tool.extract_tags(request)
        tags.tool_types = tool_types
        tags.tool_details = tool_details

    # 关键词分类
    keyword_rules = _config.routing.get("keyword_routing", {}).get("rules", []) if _config else []
    keyword_classifier = KeywordClassifier()
    tags.keywords = keyword_classifier.extract_tags(request, keyword_rules)

    return tags


def _detect_workflow_intent(body: dict, keywords: dict) -> RoutingDecision:
    """基于 splitter 检测工作流意图，返回完整路由决策。

    优化：会话标题生成等一次性 completion（Claude Code 会把会话内容包在
    <session> 中并要求生成 title）不需要语义意图路由，直接走标准默认路由，
    避免为无关请求加载昂贵的 embedding 模型（m3e-small / bge-m3）。
    """
    global _workflow_splitter, _workflow_stage_splitter, _config
    if _is_title_generation_request(body):
        default = (_config.routing.get("default", "minimax:MiniMax-M2.7")
                   if _config else "minimax:MiniMax-M2.7")
        logger.info("[_detect_workflow_intent] title/summary request, skip semantic splitter, route=default")
        return RoutingDecision(intent="chat", route=default, matched_rule="non_workflow_bypass")
    # 优先使用独立的 workflow 阶段 splitter（workflow.workflow_splitter 配置创建）
    if _workflow_stage_splitter is not None:
        return _workflow_stage_splitter.detect(body)
    # 其次使用共享 splitter
    if _workflow_splitter is not None:
        return _workflow_splitter.detect(body)
    # Fallback: 旧逻辑（不应该走到这里）
    from .splitter.workflow import WorkflowSplitter
    splitter = WorkflowSplitter(config={}, keywords=keywords)
    intent = splitter.detect_intent(body)
    default = "minimax:MiniMax-M2.7"
    return RoutingDecision(intent=intent, route=default, matched_rule="workflow_splitter_fallback")


def _detect_workflow_stage(body: dict) -> str:
    """基于独立的 workflow splitter 判定当前 workflow 阶段。

    优先用 _workflow_stage_splitter.detect(body) 的 workflow_stage 字段判定；
    异常或为空则回退 _infer_stage_from_context(body)。
    """
    global _workflow_stage_splitter
    if _workflow_stage_splitter is not None:
        try:
            decision = _workflow_stage_splitter.detect(body)
            stage = getattr(decision, "workflow_stage", None)
            if stage:
                logger.info(f"[_detect_workflow_stage] splitter 判定 workflow_stage={stage}")
                return stage
            logger.info("[_detect_workflow_stage] splitter 未命中 workflow_stage，回退上下文推断")
        except Exception as e:
            logger.warning(f"[_detect_workflow_stage] splitter 判定失败: {e}，回退上下文推断")
    return _infer_stage_from_context(body)


def _resolve_execute_route(body: dict, request_id: str, stage: str = "execute_solve") -> str:
    """根据路由规则动态决定某个 workflow 阶段走哪个 provider:model。

    复用标准路由引擎的 scenario/tool_routing/keyword_routing 规则，
    如果路由引擎能匹配到规则，就用其结果；否则按 stage 兜底到对应 workflow 列表首项。
    """
    global _routing_engine, _config

    try:
        tags = _classify_request(body, stage=stage)
        route_result = _routing_engine.route(tags)
        route_str = f"{route_result.provider}:{route_result.model}"
        logger.info(
            f"[{request_id}] {stage} routed to {route_str} "
            f"via {route_result.matched_rule} ({route_result.matched_reason})"
        )
        return route_str
    except Exception as e:
        logger.warning(f"[{request_id}] {stage} routing failed ({e}), using workflow default")
        if stage == "intention_analyze":
            lst = _config.workflow.get_intention_analyze_list()
        else:
            lst = _config.workflow.get_execute_solve_list()
        return lst[0] if lst else _config.workflow.get_execute_solve_single()


def _get_stage_routes(stage: str, body: dict = None) -> tuple[list[str], str, dict]:
    """根据 workflow 阶段获取路由列表和步骤名

    Args:
        stage: workflow 阶段名称（intention_analyze/chat_intention/analyze_plan/execute_solve）
        body: 原始请求体（用于 scenario 检测）

    Returns:
        (route_list, step_name, route_meta): 路由列表（包含 fallback）、步骤名、路由元信息
        route_meta = {"matched_rule": str, "matched_keyword": str}
    """
    global _config, _routing_engine

    def _meta(rule: str, kws: list[str] = None) -> dict:
        return {"matched_rule": rule or "", "matched_keyword": ",".join(kws) if kws else ""}

    # 优先检查 /compact 和 lv1 scenario（compact / long_context / image）
    if body:
        # /compact 命令（仅纯 compact，合并发言不在此拦截）
        if _is_pure_compact_request(body):
            compact_config = _config.routing.get("scenarios", {}).get("compact", {})
            route = compact_config.get("route", "minimax_long:MiniMax-M3")
            fallback = compact_config.get("fallback", [])
            logger.info(f"/compact request detected, routing to {route}")
            return [route] + fallback, "compact", _meta("scenario.compact")

        if _is_toolssearch_request(body):
            matched = _match_tool_routing_for_body(body)
            if matched:
                route_str, fb, rule_name = matched
                logger.info(f"[toolsearch] request detected, routing to {route_str} via {rule_name}")
                return [route_str] + list(fb or []), "tool_routing", _meta(f"tool_routing.{rule_name}")
            # 没匹配到 rule：fallback 到默认 tool 路由
            fallback_rule = _config.routing.get("tool_routing", {}).get("cheap_tasks") or {}
            route = fallback_rule.get("route", "minimax_long:MiniMax-M3")
            fb = fallback_rule.get("fallback", [])
            logger.info(f"[toolsearch] request detected (no matching rule), fallback to {route}")
            return [route] + fb, "tool_routing", _meta("tool_routing.cheap_tasks")



        # scenario 检测（long_context / image）
        try:
            tags = _classify_request(body)
        except Exception:
            tags = None
        if tags and tags.scenario !='think':
            scenarios_cfg = _config.routing.get("scenarios", {})
            cfg = scenarios_cfg.get(tags.scenario, {})
            route = cfg.get("route", "")
            fallback = cfg.get("fallback", [])
            if route:
                logger.info(f"Scenario '{tags.scenario}' detected, routing to {route}")
                return [route] + fallback, tags.scenario, _meta(f"scenario.{tags.scenario}", tags.keywords)

    # intention_analyze / analyze_plan 直接用 workflow 配置，跳过 splitter 和路由引擎
    if stage == "intention_analyze":
        wf_list = _config.workflow.get_intention_analyze_list()
        _meta_workflow=_meta("workflow.intention_analyze")
        return wf_list, "intention_analyze",_meta_workflow
    elif stage == "analyze_plan":
        plan_list = _config.workflow.get_analyze_plan_list()
        _meta_workflow =  _meta("workflow.analyze_plan")
        return plan_list, "analyze_plan", _meta_workflow


    # execute_solve / chat_intention：优先用 splitter 决策，splitter 没命中时再走场景/工具/关键词规则
    splitter_decision = _try_splitter_route(body)
    if splitter_decision:
        resolved, meta = splitter_decision
        wf_list = _get_workflow_list(stage)
        fallback = [r for r in wf_list if r != resolved]
        return [resolved] + fallback, stage, meta
    elif stage == "chat_intention":
        return _config.workflow.get_chat_intention_list(), "chat_intention", _meta("workflow.chat_intention")
    elif stage == "execute_solve":
        resolved, meta = _resolve_route_with_meta(body, "execute_solve")
        wf_list = _config.workflow.get_execute_solve_list()
        fallback = [r for r in wf_list if r != resolved]
        return [resolved] + fallback, "execute_solve", meta
    elif tags and tags.scenario == 'think':
        scenarios_cfg = _config.routing.get("scenarios", {})
        cfg = scenarios_cfg.get(tags.scenario, {})
        route = cfg.get("route", "")
        fallback = cfg.get("fallback", [])
        if route:
            logger.info(f"Scenario '{tags.scenario}' detected, routing to {route}")
            return [route] + fallback, tags.scenario, _meta(f"scenario.{tags.scenario}", tags.keywords)
    elif stage == "tool_use":
        plan_list = _config.workflow.get_analyze_plan_list()
        _meta_workflow = _meta("workflow.analyze_plan")
        return plan_list, "analyze_plan", _meta_workflow

    else:
        # 未知阶段，默认 execute_solve
        logger.warning(f"Unknown workflow_stage: {stage}, defaulting to execute_solve")
        return _config.workflow.get_execute_solve_list(), "execute_solve", _meta("workflow.execute_solve")


def _get_workflow_list(stage: str) -> list[str]:
    """根据 stage 取对应 workflow 列表。"""
    global _config
    if stage == "intention_analyze":
        return _config.workflow.get_intention_analyze_list()
    elif stage == "chat_intention":
        return _config.workflow.get_chat_intention_list()
    elif stage == "analyze_plan":
        return _config.workflow.get_analyze_plan_list()
    elif stage == "execute_solve":
        return _config.workflow.get_execute_solve_list()
    return _config.workflow.get_execute_solve_list()


def _try_splitter_route(body: dict) -> tuple[str, dict] | None:
    """尝试用 splitter（语义/关键词/LLM）决策决定首项路由。

    splitter 真正命中关键词时（matched_reason 不含 no_match/no_keywords）
    才使用 splitter 决策，避免每次都走语义路径。
    若 splitter 选择的 provider 不满足请求能力（vision/thinking），走降级链；
    降级链都不满足则返回 None，让上层 fallback 到 _resolve_route_with_meta。
    """
    global _workflow_splitter, _config, _registry
    if not _workflow_splitter or not body:
        return None
    try:
        decision = _workflow_splitter.detect(body)
    except Exception as e:
        logger.warning(f"splitter detect failed: {e}")
        return None

    reason = decision.matched_reason or ""
    # splitter 没命中关键词（no_match / no_keywords_matched），不优先用
    if "no_match" in reason or "no_keywords" in reason:
        logger.debug(f"splitter not matched ({reason}), fallback to routing engine")
        return None

    route_str = decision.route or ""
    if ":" not in route_str:
        return None
    prov_name, model = route_str.split(":", 1)

    # 能力检查：若 splitter 选的 provider 满足 vision/thinking 要求，直接用；
    # 否则尝试 splitter.fallback 中的降级链
    try:
        tags = _classify_request(body)
    except Exception:
        tags = None

    candidates = [route_str]
    if decision.fallback:
        candidates.extend(decision.fallback)

    chosen = None
    for cand in candidates:
        if ":" not in cand:
            continue
        p_name, _ = cand.split(":", 1)
        prov = _registry.get(p_name)
        if not prov:
            continue
        caps = prov.capabilities or {}
        if tags and tags.has_images and not caps.get("vision", False):
            continue
        if tags and tags.has_thinking and not caps.get("thinking", False):
            continue
        chosen = cand
        break

    if not chosen:
        logger.info(f"splitter route {route_str} (fallback={decision.fallback}) cannot satisfy capabilities, fallback to routing engine")
        return None

    # 解析 matched_keyword
    matched_keyword = ""
    if decision.matched_rule == "keyword_routing":
        import re as _re
        m = _re.search(r"keywords=(\[.*?\])", reason)
        if m:
            try:
                kws = eval(m.group(1), {"__builtins__": {}}, {})
                matched_keyword = ",".join(kws) if isinstance(kws, list) else str(kws)
            except Exception:
                matched_keyword = reason

    meta = {
        "matched_rule": decision.matched_rule or "",
        "matched_keyword": matched_keyword,
    }
    logger.info(f"splitter decision: {chosen} via {decision.matched_rule} ({reason})")
    return chosen, meta


def _resolve_route_with_meta(body: dict, stage: str) -> tuple[str, dict]:
    """与 _resolve_execute_route 类似，但额外返回路由元信息（matched_rule / matched_keyword）。

    Returns:
        (route_str, route_meta)
    """
    global _routing_engine, _config
    try:
        tags = _classify_request(body, stage=stage)
        route_result = _routing_engine.route(tags)
        route_str = f"{route_result.provider}:{route_result.model}"
        logger.info(
            f"[{stage}] routed to {route_str} "
            f"via {route_result.matched_rule} ({route_result.matched_reason})"
        )
        matched_rule = route_result.matched_rule or ""
        matched_keyword = ""
        if route_result.matched_reason:
            if matched_rule == "keyword_routing":
                # keyword_routing: matched_reason 形如 "keyword=['优化', '设计']"
                import re as _re
                m = _re.search(r"keyword=(\[.*?\])", route_result.matched_reason)
                if m:
                    try:
                        kws = eval(m.group(1), {"__builtins__": {}}, {})
                        matched_keyword = ",".join(kws) if isinstance(kws, list) else str(kws)
                    except Exception:
                        matched_keyword = route_result.matched_reason
            elif matched_rule.startswith("tool_routing."):
                # tool_routing: matched_reason 形如 "tool=Read()"
                import re as _re
                m = _re.search(r"tool=(\w+)", route_result.matched_reason)
                if m:
                    matched_keyword = m.group(1)
            else:
                matched_keyword = route_result.matched_reason
        meta = {"matched_rule": matched_rule, "matched_keyword": matched_keyword}
        return route_str, meta
    except Exception as e:
        logger.warning(f"{stage} routing failed ({e}), using workflow default")
        if stage == "intention_analyze":
            lst = _config.workflow.get_intention_analyze_list()
        else:
            lst = _config.workflow.get_execute_solve_list()
        route_str = lst[0] if lst else _config.workflow.get_execute_solve_single()
        return route_str, {"matched_rule": f"workflow.{stage}", "matched_keyword": ""}


_WORKFLOW_STAGES = {"intention_analyze", "execute_solve", "analyze_plan", "execute_write"}


def _extract_stage_from_tag(body: dict) -> str | None:
    """从 assistant 消息中的 {workflow_stage:xxx} tag 提取当前 workflow 阶段。

    CCRG 在每个阶段完成后，会在响应末尾注入 {workflow_stage:<next_stage>} 可见 tag。
    Claude Code 会将其保留在对话历史中，下一个请求时 CCRG 从最后一条 assistant 消息中提取。
    以最后面的 workflow_stage 为准。
    """
    import re
    messages = body.get("messages", [])

    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            break
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                matches = re.findall(r'\{workflow_stage:(\w+)\}', text)
                if matches:
                    # 以最后一个为准
                    stage = matches[-1]
                    if stage in _WORKFLOW_STAGES:
                        return stage
        break  # 只检查最后一条 assistant 消息
    return None


def _infer_stage_from_context(body: dict) -> str:
    """从 conversation 内容推断当前 workflow 阶段。

    推断优先级：
    1. [CCRG:STAGE:xxx] tag（最可靠，由 CCRG 注入）
    2. tool_use 模式推断（兜底）

    Claude Code 的 workflow 流程：
    1. intention_analyze: 分析用户意图（用户主动输入）
    2. execute_solve: 执行解决方案（工具回调）
    3. analyze_plan: 分析执行结果（工具回调）
    4. execute_write: 写入结果（工具回调）
    """
    # 优先：从 tag 提取
    stage = _extract_stage_from_tag(body)
    if stage:
        return stage

    # 兜底：从 tool_use 模式推断
    messages = body.get("messages", [])

    last_assistant = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg
            break

    if not last_assistant:
        return "execute_solve"

    # 检查是否有 tool_use（说明 assistant 调用了工具，应该进入下一个阶段）
    has_tool_use = False
    if isinstance(last_assistant.get("content"), list):
        for block in last_assistant["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                has_tool_use = True
                break

    if has_tool_use:
        return "tool_use"

    return "execute_solve"


def _is_title_generation_request(body: dict) -> bool:
    """判断请求是否为会话标题/摘要类一次性 completion。

    Claude Code 的会话标题生成会把会话内容包在 <session>...</session> 中，
    且 system 提示要求生成 title（"Generate a concise, sentence-case title"）。
    这类请求不是 CCR 工作流步骤，不需要语义意图路由，应跳过 embedding 模型。
    """
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        texts = []
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
        for t in texts:
            tl = t.lower()
            if "<session>" in tl or "generate a concise" in tl or "generate a title" in tl:
                return True
    return False


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


# Claude Code 命令包装 / 系统回显文本标记：这些块不算用户真实发言
_COMPACT_SYSTEM_MARKERS = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "[Request interrupted by user]",
    "<system-reminder>",
)

# 全量标记：紧凑标记 + 工具结果/系统指令块等更长模式
_ALL_SYSTEM_MARKERS = (
    # 继承紧凑标记
    *(_COMPACT_SYSTEM_MARKERS),
    # 工具结果
    "Tool Results:",
    "<tool-result>",
    "</tool-result>",
    # 系统指令块
    "Memory:",
    "memory:",
    "Skills:",
    "skills:",
    "General:",
    "general:",
)


def _is_pure_compact_request(body: dict) -> bool:
    """判断请求是否是"纯 /compact 命令"。

    新版 Claude Code 会把 /compact 命令、执行回显与 /compact 之后的用户
    真实发言合并进同一条 user 消息（如 req_cfd6a5cc：block[3] 为命令包装、
    block[5]="继续" 为真实发言）。本函数区分两种情形：
    - 纯 /compact 命令（消息内除命令包装/系统回显外无用户真实发言）→ True
    - /compact 命令 + 用户后续发言合并 → False（应走正常意图分析，不吞发言）

    /compact 请求应直接透传给模型，不走 analyze_plan/execute_solve 流程。
    """
    messages = body.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        texts: list[str] = []
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]

        has_compact_cmd = False
        has_real_speech = False
        for t in texts:
            if "/compact" in t:
                has_compact_cmd = True
            stripped = t.strip()
            if not stripped:
                continue
            if any(m in stripped for m in _COMPACT_SYSTEM_MARKERS):
                continue
            has_real_speech = True

        if has_compact_cmd:
            return not has_real_speech
        break  # 只检查最后一条 user 消息
    return False


def _is_toolssearch_request(body: dict) -> bool:
    """判断请求是否是 toolsearch（系统自动触发的 ToolSearch 调用，而非用户主动发言）"""
    messages = body.get("messages", [])

    # 1. 找到最后一条 user 消息
    last_user_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg
            break
    if last_user_msg is None:
        return False

    # 2. 提取所有 text 段
    content = last_user_msg.get("content", "")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        texts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
    else:
        texts = []

    # 3. 逐段分类：ToolSearch 命令 / 系统标记 / 用户真实发言
    has_toolsearch_cmd = False
    has_real_speech = False

    for t in texts:
        if "Tool: ToolSearch" in t:
            has_toolsearch_cmd = True
            continue  # ToolSearch 命令本身不算用户发言

        stripped = t.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in _ALL_SYSTEM_MARKERS):
            continue  # 系统注入的标记不算用户发言

        has_real_speech = True  # 这是用户自己说的话

    # 4. 只有"纯系统注入"的 ToolSearch 才算
    return has_toolsearch_cmd and not has_real_speech


def _match_tool_routing_for_body(body: dict) -> tuple[str, list[str] | None, str] | None:
    """扫描 tool_routing 配置，按请求中 tool 名称匹配，返回第一个命中的 (route, fallback, rule_name)。

    - 工具名从 request 的 tools 字段读（Anthropic 格式）
    - 也兼容从最近一条 assistant 消息的 tool_use 块读取
    - tool_routing 中每个 rule 的 match 字段是允许的 tool 名列表
    """
    tool_names: set[str] = set()

    # 1. 从 body.tools 提取（请求里明确声明的工具）
    for tool in body.get("tools", []) or []:
        if isinstance(tool, dict):
            name = tool.get("name")
            if name:
                tool_names.add(name)

    # 2. 从最近 assistant 消息的 tool_use 块提取（实际使用过的工具）
    for msg in reversed(body.get("messages", []) or []):
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if name:
                    tool_names.add(name)
        break

    if not tool_names:
        return None

    tool_routing_cfg = _config.routing.get("tool_routing", {}) if _config else {}
    for rule_name, rule in tool_routing_cfg.items():
        if rule_name.startswith("_"):  # 跳过 _default 之类
            continue
        match_list = rule.get("match", []) or []
        if any(name in match_list for name in tool_names):
            route_str = rule.get("route", "")
            if ":" not in route_str:
                continue
            return route_str, rule.get("fallback"), rule_name

    return None

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
            block_str = json.dumps(block, ensure_ascii=False)
            block_chars = len(block_str)
            if kept_chars + block_chars > max_chars and kept_blocks:
                break
            kept_blocks.append(block)
            kept_chars += block_chars

        kept_blocks.reverse()

        if len(kept_blocks) == len(content):
            return msg

        # 在截断点插入提示 block（插入到 kept_blocks 开头，而非 msg 开头）
        truncation_block = {
            "type": "text",
            "text": "<system-reminder> Earlier conversation history has been truncated to fit context window. The most recent messages are preserved. </system-reminder>"
        }
        return {**msg, "content": kept_blocks + [truncation_block]}

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
        # image 预处理：若目标 provider 不支持 vision 且含图片，先把图片转成文字描述
        messages = await _preprocess_images_for_provider(messages, prov_config, request_id)
        req_body["messages"] = messages
        req_body["model"] = model

        req_for_provider = prov_adapter.transform_request(req_body, _provider_config_to_dict(prov_config))
        req_for_provider["model"] = model

        prov_target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
        if not prov_target_url.startswith("http"):
            prov_target_url = f"http://{prov_target_url}"

        prov_headers = _build_provider_headers(prov_config)

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
            async with contextlib.nullcontext(_http_client) as client:
                response = await client.post(prov_target_url, json=req_for_provider, headers=prov_headers, timeout=prov_timeout)
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
                                success=0, route_rule=f"workflow.{step_name}",
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
                        success=1, route_rule=f"workflow.{step_name}",
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
                            success=0, route_rule=f"workflow.{step_name}",
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
                    success=0, route_rule=f"workflow.{step_name}",
                )
            return {"error": {"type": "workflow_error", "message": str(e)}}, False
        except Exception as e:
            logger.warning(f"[{request_id}] Workflow {step_name} failed: {e}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=0, route_rule=f"workflow.{step_name}",
                )
            return {"error": {"type": "workflow_error", "message": str(e)}}, False

    # 本次调用的上下文（用于重试逻辑）
    local_req_body = [dict(body)]  # 用列表包装以便在闭包中修改
    retried_strip = False  # 本次调用是否已尝试过剥离
    # 当前请求的路由元信息（matched_keyword / matched_rule），由 workflow_stream_generator 填充，
    # call_provider_streaming 读取后写入 usage_records。用 dict 便于在闭包间共享且可变。
    _route_meta = {"matched_keyword": "", "matched_rule": ""}
    attempt_index = 0  # 每次调用递增，确保每条记录唯一

    async def call_provider_streaming(route_str: str, messages: list, step_name: str, attempt_id: str = None) -> AsyncGenerator[bytes, None]:
        """流式调用 provider，实时 yield 每个 chunk"""
        nonlocal local_req_body, retried_strip, attempt_index
        prov_name, model = parse_provider_model(route_str)
        if not attempt_id:
            base_id = f"{request_id}_{prov_name}_{step_name}"
            attempt_id = f"{base_id}_{attempt_index}"
            attempt_index += 1
        prov_config = _registry.get(prov_name)
        if not prov_config:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': f'Unknown provider: {prov_name}'}}, ensure_ascii=False)}\n\n".encode("utf-8")
            return

        prov_adapter = _get_adapter_for_provider(prov_name)

        # 构建请求（使用可能已更新的 local_req_body）
        req_body = local_req_body[0]
        req_body = dict(req_body)
        # image 预处理：若目标 provider 不支持 vision 且含图片，先把图片转成文字描述
        # 避免 glm-5.2 等 text-only 模型收到 image 块返回 400 "Model only support text input"
        messages = await _preprocess_images_for_provider(messages, prov_config, request_id)
        req_body["messages"] = messages
        req_body["model"] = model
        req_body["stream"] = is_streaming

        req_for_provider = prov_adapter.transform_request(req_body, _provider_config_to_dict(prov_config))
        req_for_provider["model"] = model

        prov_target_url = prov_adapter.get_target_url(_provider_config_to_dict(prov_config), model)
        if not prov_target_url.startswith("http"):
            prov_target_url = f"http://{prov_target_url}"

        prov_headers = _build_provider_headers(prov_config)

        prov_timeout = (prov_config.timeout_ms or _config.server.get("timeout_ms", 600000)) / 1000

        # Debug: log the actual request being sent (first user msg + model)
        first_user = next((m["content"] for m in messages if m.get("role") == "user"), None)
        if isinstance(first_user, list):
            first_user = next((c["text"] for c in first_user if c.get("type") == "text"), None)
        preview = str(first_user)[:250].replace("\n", " ") if first_user else "(no user msg)"

        # Debug: 打印更多请求信息
        if logger.isEnabledFor(logging.DEBUG):
            req_body_len = len(json.dumps(req_for_provider, ensure_ascii=False))
            msgs_len = len(json.dumps(messages, ensure_ascii=False))
            msgs_tokens = msgs_len // 4
            logger.debug(f"[{request_id}] → [{prov_name}] req: model={model}, stream=True, req_len={req_body_len}, msgs_len={msgs_len}, msgs_tokens~{msgs_tokens}, first_user={preview}")
        else:
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

        # Debug: 保存完整请求体到文件，curl 用 @file 引用（避免 truncate）
        if logger.isEnabledFor(logging.DEBUG):
            req_dir = Path("logs/req")
            req_dir.mkdir(parents=True, exist_ok=True)
            req_file = req_dir / f"{request_id}_{prov_name}.json"
            with open(req_file, "w", encoding="utf-8") as f:
                json.dump(req_for_provider, f, ensure_ascii=False)
            curl_cmd = _make_curl_cmd(prov_target_url, f"logs/req/{req_file.name}", prov_config)
            logger.debug(f"[FallbackRouter] [REQ] [CURL]4 [{prov_name}] [{model}]: {req_file} (chars={len(json.dumps(req_for_provider, ensure_ascii=False))})\n{curl_cmd}")

        # 请求发出时先记录（pending 状态，success=2 表示未完成）
        if _usage_stats:
            _usage_stats.record(
                provider=prov_name, model=model,
                input_tokens=0, output_tokens=0,
                latency_ms=0,
                success=2, route_rule=f"workflow.{step_name}",
                attempt_id=attempt_id,
                matched_keyword=_route_meta["matched_keyword"],
                matched_rule=_route_meta["matched_rule"],
            )

        try:
            async with contextlib.nullcontext(_http_client) as client:
                async with client.stream("POST", prov_target_url, json=req_for_provider, headers=prov_headers, timeout=prov_timeout) as response:
                    response.raise_for_status()

                    # Debug: 追踪响应内容用于日志
                    resp_first_chunk = None
                    resp_last_chunk = None
                    resp_chunk_count = 0
                    resp_raw_lines = []  # 保存前几条原始行用于调试

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
                                    # Debug: 追踪响应内容
                                    if resp_first_chunk is None:
                                        resp_first_chunk = data_content[:200]
                                    resp_last_chunk = data_content[:200]
                                    resp_chunk_count += 1
                                    yield event
                                if data_content == "[DONE]":
                                    break
                            else:
                                # Debug: 追踪响应内容
                                if resp_first_chunk is None:
                                    resp_first_chunk = data_content[:200]
                                resp_last_chunk = data_content[:200]
                                resp_chunk_count += 1
                                # 直接 yield 原始数据
                                if data_content == "[DONE]":
                                    break

                                # 检查是否是错误响应（HTTP 200 但 body 是 error JSON）
                                if data_content.startswith("{"):
                                    try:
                                        chunk_data = json.loads(data_content)
                                        if "error" in chunk_data:
                                            err_info = chunk_data["error"]
                                            err_code = err_info.get("code", "")
                                            err_msg = err_info.get("message", "")
                                            raise RuntimeError(f"{prov_name} stream returned error: {err_code} - {err_msg}")
                                    except RuntimeError:
                                        raise
                                    except Exception:
                                        pass

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
                    success=1, route_rule=f"workflow.{step_name}",
                    attempt_id=attempt_id,
                    matched_keyword=_route_meta["matched_keyword"],
                    matched_rule=_route_meta["matched_rule"],
                )
            # chunks=0 表示返回了空响应（200 OK 但 body 为空），视为失败，触发 fallback
            if resp_chunk_count == 0:
                logger.warning(f"[{request_id}] {prov_name} returned empty response (chunks=0), triggering fallback")
                if _usage_stats:
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        success=0, route_rule=f"workflow.{step_name}",
                        attempt_id=attempt_id,
                        matched_keyword=_route_meta["matched_keyword"],
                        matched_rule=_route_meta["matched_rule"],
                    )
                raise RuntimeError(f"{prov_name} returned empty response (chunks=0)")

            logger.debug(f"[{request_id}] ← [{prov_name}] stream completed, step={step_name}")
            logger.debug(f"[FallbackRouter] [RESULT] [REPONSE] [{prov_name}] step={step_name}, status=200 OK, chunks={resp_chunk_count}, first={resp_first_chunk or 'none'}, last={resp_last_chunk or 'none'}, input_tokens={input_tokens}, output_tokens={output_tokens}")

        except HTTPStatusError as e:
            # 记录详细的错误响应信息
            error_text = ""
            try:
                # 流式响应不能直接 .text，需要先 aread
                await e.response.aread()
                error_text = e.response.text
                logger.error(f"[{request_id}] {prov_name} streaming returned {e.response.status_code} error: {error_text[:500]}")
            except Exception as log_err:
                # aread 失败，尝试从异常消息中提取 error body
                error_text = str(e)
                # 尝试提取 body 部分（格式: "...body b'...'")
                import re
                body_match = re.search(r"body b'(.*?)'", error_text)
                if body_match:
                    import base64
                    try:
                        error_text = body_match.group(1).encode().decode("unicode_escape")
                    except Exception:
                        pass
                logger.error(f"[{request_id}] {prov_name} streaming {e.response.status_code}, err from exception: {error_text[:500]}")

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

                # Debug: 打印完整的 curl 命令
                if logger.isEnabledFor(logging.DEBUG):
                    req_dir = Path("logs/req")
                    req_dir.mkdir(parents=True, exist_ok=True)
                    req_file = req_dir / f"{request_id}_{prov_name}_400.json"
                    with open(req_file, "w", encoding="utf-8") as f:
                        json.dump(req_for_provider, f, ensure_ascii=False)
                    curl_cmd = _make_curl_cmd(prov_target_url, f"logs/req/{req_file.name}", prov_config)
                    logger.debug(f"[FallbackRouter] [REQ] [CURL]5 [{prov_name}] [{model}]: {req_file} (chars={len(json.dumps(req_for_provider, ensure_ascii=False))})\n{curl_cmd}")

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
                            success=0, route_rule=f"workflow.{step_name}",
                            attempt_id=attempt_id,
                            matched_keyword=_route_meta["matched_keyword"],
                            matched_rule=_route_meta["matched_rule"],
                        )
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': {'type': 'invalid_request_error', 'message': user_msg}}, ensure_ascii=False)}\n\n".encode("utf-8")
                    return

                # 非 context 超限的 400 错误（如 invalid params）
                # 抛出异常让 FallbackRouter 重试
                if not retried_strip:
                    stripped = _strip_unsupported_features(err_msg, req_for_provider, prov_config.protocol)
                    if stripped is not req_for_provider:
                        retried_strip = True
                        logger.info(f"[{request_id}] {prov_name} doesn't support some features, stripping and retrying")
                        # Debug: 保存剥离后的请求体到文件
                        if logger.isEnabledFor(logging.DEBUG):
                            req_dir = Path("logs/req")
                            req_dir.mkdir(parents=True, exist_ok=True)
                            req_file = req_dir / f"{request_id}_{prov_name}_strip.json"
                            with open(req_file, "w", encoding="utf-8") as f:
                                json.dump(stripped, f, ensure_ascii=False)
                            curl_cmd = _make_curl_cmd(prov_target_url, f"logs/req/{req_file.name}", prov_config)
                            logger.debug(f"[FallbackRouter] [REQ] [CURL]6 [{prov_name}]: {req_file} (chars={len(json.dumps(stripped, ensure_ascii=False))})\n{curl_cmd}")
                        # 更新 local_req_body 用于下次重试
                        local_req_body[0] = stripped
                        # 剥离后跳到下一个 provider 重试前，先终结本次 pending 记录
                        # （否则 success=2 会一直留着，因为下面的 record(success=0) 不会被执行）
                        if _usage_stats:
                            _usage_stats.record(
                                provider=prov_name, model=model,
                                input_tokens=0, output_tokens=0,
                                latency_ms=(time.time() - start_time) * 1000,
                                success=0, route_rule=f"workflow.{step_name}",
                                attempt_id=attempt_id,
                                matched_keyword=_route_meta["matched_keyword"],
                                matched_rule=_route_meta["matched_rule"],
                            )
                        # 抛出异常让 FallbackRouter 重试
                        raise Exception(f"{prov_name} stripped unsupported features, will retry")

                # 打印完整响应体供调试（error_text 在上面 aread() 时已读取）
                err_body_str = error_text if error_text else str(e)
                logger.warning(f"[{request_id}] {prov_name} streaming 400 response body: {err_body_str[:500]}")
                logger.warning(f"[{request_id}] {prov_name} streaming 400 (not context error): {(error_text[:200] if error_text else str(e)[:200])}")
                if _usage_stats:
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        success=0, route_rule=f"workflow.{step_name}",
                        attempt_id=attempt_id,
                        matched_keyword=_route_meta["matched_keyword"],
                        matched_rule=_route_meta["matched_rule"],
                    )
                raise RuntimeError(f"{prov_name} streaming returned 400: {err_msg[:300]}") from e

            # 429 Too Many Requests → 抛异常让调用方 fallback 到其他 provider
            if e.response.status_code == 429:
                logger.warning(f"[{request_id}] Workflow {step_name} hit rate limit (429) from {prov_name}")
                logger.debug(f"[FallbackRouter] [RESULT] [REPONSE] [{prov_name}] status=429")
                if _usage_stats:
                    _usage_stats.record(
                        provider=prov_name, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=(time.time() - start_time) * 1000,
                        success=0, route_rule=f"workflow.{step_name}",
                        attempt_id=attempt_id,
                        matched_keyword=_route_meta["matched_keyword"],
                        matched_rule=_route_meta["matched_rule"],
                    )
                raise RuntimeError(f"{prov_name} streaming returned 429 rate limit") from e

            # 其他 HTTP 错误 → 也抛异常让调用方 fallback
            logger.warning(f"[{request_id}] {prov_name} streaming returned {e.response.status_code}: {error_text[:200]}")
            logger.debug(f"[FallbackRouter] [RESULT] [REPONSE] [{prov_name}] status={e.response.status_code}, error={error_text[:200] if error_text else str(e)}")
            if _usage_stats:
                _usage_stats.record(
                    provider=prov_name, model=model,
                    input_tokens=0, output_tokens=0,
                    latency_ms=(time.time() - start_time) * 1000,
                    success=0, route_rule=f"workflow.{step_name}",
                    attempt_id=attempt_id,
                    matched_keyword=_route_meta["matched_keyword"],
                    matched_rule=_route_meta["matched_rule"],
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
                    success=0, route_rule=f"workflow.{step_name}",
                    attempt_id=attempt_id,
                    matched_keyword=_route_meta["matched_keyword"],
                    matched_rule=_route_meta["matched_rule"],
                )
            raise

    # Step 1: Intention Analysis (基于 splitter) - 返回 RoutingDecision
    routing_decision = _detect_workflow_intent(body, _config.keywords)
    intent = routing_decision.intent
    stage_route = routing_decision.route  # splitter 返回的路由
    is_chat = (intent == "chat")
    is_user_initiated = _is_user_initiated_message(body)
    logger.info(f"[{request_id}] Workflow routing decision: intent={intent}, route={stage_route}, "
                f"matched_rule={routing_decision.matched_rule}, user_initiated={is_user_initiated}")

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
            routing_decision = _detect_workflow_intent(body, _config.keywords)
            intent = routing_decision.intent
            is_chat = (intent == "chat")
            is_user_initiated = _is_user_initiated_message(body)

            if is_user_initiated:
                stage = "intention_analyze"  # 用户输入 -> 意图分析（每次都走）
            else:
                # AI 自身后续请求（工具回调等非用户发言）：用独立 workflow_splitter 判断阶段，
                # 无法判定时回退 _infer_stage_from_context
                stage = _detect_workflow_stage(body)

            logger.info(f"[{request_id}] Workflow stage (auto-detected): {stage}")
        else:
            logger.info(f"[{request_id}] Workflow stage (from metadata): {stage}")

        # 3. 根据阶段选择路由和 fallback
        route_list, step_name, route_meta = _get_stage_routes(stage, body)
        _route_meta["matched_keyword"] = route_meta.get("matched_keyword", "")
        _route_meta["matched_rule"] = route_meta.get("matched_rule", "")

        msgs = body.get("messages", [])

        # Debug: 打印请求详情和路由信息
        if logger.isEnabledFor(logging.DEBUG):
            msgs_chars = len(json.dumps(msgs, ensure_ascii=False))
            msgs_tokens = msgs_chars // 4
            logger.debug(f"[{request_id}] Request stats: msgs_chars={msgs_chars}, msgs_tokens~{msgs_tokens}, msgs_count={len(msgs)}")
            # 打印路由决策
            logger.debug(f"[{request_id}] Routing: stage={stage}, intent={intent}, user_initiated={is_user_initiated}")
            logger.debug(f"[{request_id}] Route list: {route_list}")

        # /compact 请求特殊处理（仅纯 compact，合并发言不覆盖 stage）
        is_compact = _is_pure_compact_request(body)
        if is_compact:
            logger.info(f"[{request_id}] /compact request → direct pass-through, overriding stage to execute_solve")
            route_list, step_name, route_meta = _get_stage_routes("execute_solve", body)
            _route_meta["matched_keyword"] = route_meta.get("matched_keyword", "")
            _route_meta["matched_rule"] = route_meta.get("matched_rule", "")

        # 4. intention_analyze 阶段嵌入 workflow prompt
        if step_name == "intention_analyze":
            workflow_prompt = """流程：intention_analyze → execute_solve → analyze_plan → execute_write
- "{workflow_stage:intention_analyze}": 分析用户意图阶段
- "{workflow_stage:execute_solve}": 执行解决方案阶段
- "{workflow_stage:analyze_plan}": 分析执行结果阶段
- "{workflow_stage:execute_write}": 写入结果阶段
返回时：请用 {workflow_stage:execute_solve} 等标识你下一步想继续分析，还是执行解决方案，还是写入结果

"""
            processed_msgs = []
            for i, msg in enumerate(msgs):
                if msg.get("role") == "user" and i == 0:
                    original_content = msg.get("content", "")
                    if isinstance(original_content, str):
                        processed_msgs.append({**msg, "content": workflow_prompt + original_content})
                    else:
                        processed_msgs.append(msg)
                else:
                    processed_msgs.append(msg)
            msgs = processed_msgs

        # 5. 流式调用 provider（带 fallback）
        router = FallbackRouter(route_list, request_id, step_name)
        router.log_route_hit("RouteList", str(route_list))

        all_failed = True  # unused but kept for compatibility
        last_error = None
        last_error_type = "provider_error"

        async def wrapped_call(route: str, msgs: list, step_name: str):
            nonlocal last_error, last_error_type
            prov_name, _ = parse_provider_model(route)
            attempt_id = f"{request_id}_{prov_name}_{step_name}"
            try:
                async for chunk in call_provider_streaming(route, msgs, step_name, attempt_id):
                    yield chunk
            except Exception as e:
                last_error = e
                if "429" in str(e) or "rate limit" in str(e).lower():
                    last_error_type = "rate_limit_exceeded"
                elif "context length" in str(e).lower():
                    last_error_type = "context_length_exceeded"
                raise

        try:
            async for chunk in router.call_provider_streaming(wrapped_call, msgs):
                all_failed = False
                yield chunk
            # 阶段完成后，注入 {workflow_stage:xxx} tag 告诉 CCRG 下一步该设什么 stage
            # （仅对语义分块路由结果注入；keyword_routing / non_workflow_bypass 命中属一次性
            #  completion（如会话标题生成），不应向响应污染 workflow_stage tag）
            if routing_decision.matched_rule not in ("keyword_routing", "non_workflow_bypass"):
                _stage_hints = {
                    "intention_analyze": "execute_solve",
                    "execute_solve": "analyze_plan",
                    "analyze_plan": "execute_write",
                }
                next_stage = _stage_hints.get(step_name)
                if next_stage:
                    tag = f"\n\n{{workflow_stage:{next_stage}}}"
                    tag_sse = f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': tag}})}\n\n"
                    yield tag_sse.encode()
            # 正常返回，不走下面的 error 处理
            yield b"data: [DONE]\n\n"
            return

        except Exception:
            error_msg = str(last_error) if last_error else f"All {len(route_list)} providers failed"
            logger.error(f"[{request_id}] {error_msg}")
            yield _make_streaming_error_sse({"error": {"type": last_error_type, "message": error_msg}})

    return StreamingResponse(
        _wrap_with_concurrency(workflow_stream_generator, request_id),
        media_type="text/event-stream",
    )


def _convert_openai_to_anthropic(body: dict) -> dict:
    """将 OpenAI Chat Completions 格式转换为 Anthropic Messages 格式"""
    result = {
        "model": body.get("model", "MiniMax-M2.7"),
        "stream": body.get("stream", False),
    }

    # 处理 messages
    messages = body.get("messages", [])
    anthropic_messages = []
    system_prompt = None

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # 提取 system prompt
        if role == "system":
            system_prompt = content if isinstance(content, str) else None
            continue

        # 处理 tool 角色：转为 user 消息 + tool_result（跳过 normal append）
        if role == "tool":
            tool_use_id = msg.get("tool_call_id", "unknown")
            tool_content = content or ""
            if isinstance(tool_content, list):
                tool_content = " ".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in tool_content
                )
            tool_msg = {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": str(tool_content)
                }]
            }
            anthropic_messages.append(tool_msg)
            continue

        # 转换 content
        if isinstance(content, list):
            # OpenAI 的 multi-modal content 转为 Anthropic 格式
            anthropic_content = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        anthropic_content.append({"type": "text", "text": item.get("text", "")})
                    elif item.get("type") == "image_url":
                        # 提取 URL 或 base64
                        img_data = item.get("image_url", {})
                        url = img_data.get("url", "") if isinstance(img_data, dict) else ""
                        if url.startswith("data:"):
                            # data:image/png;base64,xxxxx
                            media_type = url.split(";")[0].replace("data:", "") if url else "image/jpeg"
                            b64 = url.split(",", 1)[1] if "," in url else ""
                            anthropic_content.append({"type": "image", "source": {"type": "base64", "data": b64, "media_type": media_type}})
                        else:
                            anthropic_content.append({"type": "image", "source": {"type": "base64", "data": "", "media_type": "image/jpeg"}})
                # 非 dict 类型忽略
            anthropic_messages.append({"role": role, "content": anthropic_content or [{"type": "text", "text": ""}]})
        elif isinstance(content, str):
            anthropic_messages.append({"role": role, "content": [{"type": "text", "text": content}]})
        else:
            anthropic_messages.append({"role": role, "content": [{"type": "text", "text": str(content or "")}]})

        # 处理 tool_calls：追加到当前 assistant 消息的 content 中
        tool_calls = msg.get("tool_calls", [])
        if tool_calls and role == "assistant":
            for tc in tool_calls:
                func = tc.get("function", {})
                try:
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                anthropic_messages[-1]["content"].append({
                    "type": "tool_use",
                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
                    "name": func.get("name", "unknown"),
                    "input": args
                })

    result["messages"] = anthropic_messages

    # 设置 system prompt
    if system_prompt:
        result["system"] = system_prompt

    # 处理其他参数
    if "max_tokens" in body:
        result["max_tokens"] = body["max_tokens"]

    if "thinking" in body:
        result["thinking"] = body["thinking"]

    if "tools" in body:
        # 转换 OpenAI tools 格式为 Anthropic tools 格式
        anthropic_tools = []
        for tool in body["tools"]:
            if isinstance(tool, dict) and tool.get("type") == "function":
                func = tool.get("function", {})
                anthropic_tools.append({
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })
            elif isinstance(tool, dict):
                anthropic_tools.append(tool)
        result["tools"] = anthropic_tools

    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, str):
            result["tool_choice"] = {"type": "auto"}
        elif isinstance(tc, dict) and tc.get("type") == "function":
            result["tool_choice"] = {"type": "tool", "name": tc.get("name", "")}
        else:
            result["tool_choice"] = tc

    return result


def run(host: str | None = None, port: int | None = None, config_path: str | None = None):
    import uvicorn
    import asyncio

    init_app(config_path)

    host = host or (_config.server.get("host", "127.0.0.1") if _config else "127.0.0.1")
    port = port or (_config.server.get("port", 3458) if _config else 3458)

    logger.info(f"Starting CCRG on {host}:{port}")

    # ✅✅✅ 调试绝杀：强制关闭 loop_factory ✅✅✅
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        timeout_graceful_shutdown=5
    )
    # 强制删掉冲突参数，让 PyCharm 调试无法报错
    config.loop_factory = None

    server = uvicorn.Server(config)
    asyncio.run(server.serve())

if __name__ == "__main__":
    run()