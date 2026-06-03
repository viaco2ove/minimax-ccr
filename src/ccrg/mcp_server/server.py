"""
CCRG MCP Server — 给 qoder IDE 等 MCP 客户端提供调用 CCRG 的能力。

工具列表：
- ccrg_chat: 发送 Chat Completion 请求，自动路由到最优 provider
- ccrg_route: 预览路由决策（不实际发送请求）
- ccrg_stats: 获取使用统计
- ccrg_health: 检查 CCRG 服务健康状态

使用方式：
  方式1 — 集成到 CCRG 主服务（推荐）：
    在 main.py 中调用 register_routes(app, base_url_provider)

  方式2 — 独立运行：
    python -m src.ccrg.mcp_server
"""

import json
import sys
import logging
import asyncio
import uuid
from typing import Any, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

logger = logging.getLogger("ccrg.mcp")


# ─── MCP 工具定义 ───────────────────────────────────────────────

MCP_TOOLS = [
    {
        "name": "ccrg_chat",
        "description": "通过 CCRG 发送 Chat Completion 请求，自动路由到最优 provider/model。支持 OpenAI 和 Anthropic 两种格式。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                            "content": {"type": "string"}
                        },
                        "required": ["role", "content"]
                    },
                    "description": "对话消息列表"
                },
                "format": {
                    "type": "string",
                    "enum": ["openai", "anthropic"],
                    "description": "请求格式：openai（Chat Completions）或 anthropic（Messages API）",
                    "default": "openai"
                },
                "model": {
                    "type": "string",
                    "description": "指定模型（留空则由 CCRG 自动路由）",
                    "default": ""
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "最大输出 token 数",
                    "default": 4096
                }
            },
            "required": ["messages"]
        }
    },
    {
        "name": "ccrg_route",
        "description": "预览 CCRG 路由决策，返回会被路由到哪个 provider/model（不实际发送请求）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["role", "content"]
                    },
                    "description": "对话消息列表"
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "工具名称列表（用于 tool_routing 判断）",
                    "default": []
                }
            },
            "required": ["messages"]
        }
    },
    {
        "name": "ccrg_stats",
        "description": "获取 CCRG 使用统计（请求次数、token 用量、延迟等）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": ["1h", "today", "month", "year"],
                    "description": "时间范围",
                    "default": "today"
                }
            }
        }
    },
    {
        "name": "ccrg_health",
        "description": "检查 CCRG 服务健康状态和可用 provider 列表",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


# ─── 工具实现 ───────────────────────────────────────────────────

async def _handle_ccrg_chat(args: dict, base_url: str) -> dict:
    """发送 Chat Completion 请求"""
    messages = args.get("messages", [])
    fmt = args.get("format", "openai")
    model = args.get("model", "")
    max_tokens = args.get("max_tokens", 4096)

    if fmt == "anthropic":
        system_msg = ""
        api_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg.get("content", "")
            else:
                api_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        body: dict[str, Any] = {"messages": api_messages, "max_tokens": max_tokens, "stream": False}
        if system_msg:
            body["system"] = system_msg
        if model:
            body["model"] = model

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base_url}/v1/messages", json=body,
                                     headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"})
            resp.raise_for_status()
            result = resp.json()

        content_blocks = result.get("content", [])
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        return {"text": "\n".join(text_parts), "model": result.get("model", ""),
                "usage": result.get("usage", {}), "stop_reason": result.get("stop_reason", "")}
    else:
        body = {"messages": messages, "stream": False}
        if model:
            body["model"] = model

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base_url}/v1/chat/completions", json=body,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            result = resp.json()

        choices = result.get("choices", [])
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        return {"text": text, "model": result.get("model", ""),
                "usage": result.get("usage", {}), "finish_reason": choices[0].get("finish_reason", "") if choices else ""}


async def _handle_ccrg_route(args: dict, base_url: str) -> dict:
    """预览路由决策"""
    messages = args.get("messages", [])
    tools = args.get("tools", [])

    last_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user = msg.get("content", "")
            break

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/health")
            resp.raise_for_status()
            health = resp.json()
    except Exception as e:
        return {"error": f"CCRG 不可达: {e}"}

    result = {
        "ccrg_status": "online",
        "available_providers": health.get("providers", []),
        "message_preview": last_user[:200] if last_user else "(empty)",
        "tools_detected": tools,
        "note": "这只是预览，实际路由由 CCRG 根据配置决定"
    }

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            stats_resp = await client.get(f"{base_url}/stats?range=today")
            if stats_resp.status_code == 200:
                stats = stats_resp.json()
                result["today_stats"] = {
                    p: {"requests": d.get("request_count", 0), "tokens": d.get("total_tokens", 0)}
                    for p, d in stats.get("range", {}).items()
                }
    except Exception:
        pass

    return result


async def _handle_ccrg_stats(args: dict, base_url: str) -> dict:
    """获取使用统计"""
    range_param = args.get("range", "today")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/stats?range={range_param}")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": f"获取统计失败: {e}"}


async def _handle_ccrg_health(args: dict, base_url: str) -> dict:
    """检查健康状态"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/health")
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        return {"status": "offline", "error": f"无法连接 {base_url}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ─── 工具分发 ───────────────────────────────────────────────────

_HANDLERS = {
    "ccrg_chat": _handle_ccrg_chat,
    "ccrg_route": _handle_ccrg_route,
    "ccrg_stats": _handle_ccrg_stats,
    "ccrg_health": _handle_ccrg_health,
}


# ─── MCP JSON-RPC 协议 ─────────────────────────────────────────

async def handle_jsonrpc(request: dict, base_url: str) -> dict | None:
    """处理 MCP JSON-RPC 请求"""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ccrg-mcp-server", "version": "0.1.0"}
            }
        }
    elif method == "notifications/initialized":
        return None
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = _HANDLERS.get(tool_name)
        if not handler:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}
        try:
            result = await handler(tool_args, base_url)
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}}
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


# ─── SSE 会话管理 ───────────────────────────────────────────────

_sse_sessions: dict[str, asyncio.Queue] = {}


# ─── 注册路由（集成到 CCRG 主服务）───────────────────────────────

def register_routes(app: FastAPI, base_url_provider: Callable[[], str]):
    """把 MCP 路由注册到 CCRG 的 FastAPI app 上

    Args:
        app: CCRG 的 FastAPI 实例
        base_url_provider: 返回 CCRG base_url 的函数（如 "http://127.0.0.1:3428"）
    """

    @app.post("/mcp")
    async def mcp_jsonrpc(request: Request):
        """MCP JSON-RPC 端点"""
        body = await request.json()
        method = body.get("method", "")
        logger.info(f"[MCP] method={method}, id={body.get('id')}")
        if method == "tools/call":
            tool_name = body.get("params", {}).get("name", "")
            tool_args = body.get("params", {}).get("arguments", {})
            logger.info(f"[MCP] tool_call: {tool_name} args={json.dumps(tool_args, ensure_ascii=False)[:200]}")
        response = await handle_jsonrpc(body, base_url_provider())
        if response is None:
            return JSONResponse(content={})
        return JSONResponse(content=response)

    @app.get("/mcp/sse")
    async def mcp_sse(request: Request):
        """MCP SSE 端点"""
        session_id = str(uuid.uuid4())
        _sse_sessions[session_id] = asyncio.Queue()

        async def event_stream():
            try:
                yield f"event: endpoint\ndata: {base_url_provider()}/mcp/messages?sessionId={session_id}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(_sse_sessions[session_id].get(), timeout=30)
                        yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                _sse_sessions.pop(session_id, None)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/mcp/messages")
    async def mcp_messages(request: Request, sessionId: str = ""):
        """MCP SSE 消息端点"""
        body = await request.json()
        method = body.get("method", "")
        logger.info(f"[MCP-SSE] method={method}, id={body.get('id')}, session={sessionId[:8]}...")
        if method == "tools/call":
            tool_name = body.get("params", {}).get("name", "")
            tool_args = body.get("params", {}).get("arguments", {})
            logger.info(f"[MCP-SSE] tool_call: {tool_name} args={json.dumps(tool_args, ensure_ascii=False)[:200]}")
        response = await handle_jsonrpc(body, base_url_provider())

        if sessionId and sessionId in _sse_sessions:
            if response is not None:
                await _sse_sessions[sessionId].put(response)
            return JSONResponse(content={"status": "accepted"})
        else:
            return JSONResponse(content=response or {})

    logger.info("MCP routes registered: POST /mcp, GET /mcp/sse, POST /mcp/messages")


# ─── 独立运行 ───────────────────────────────────────────────────

def main():
    """独立运行 MCP Server（不依赖 CCRG 主服务）"""
    import argparse
    parser = argparse.ArgumentParser(description="CCRG MCP Server (standalone)")
    parser.add_argument("--ccrg-url", default="http://127.0.0.1:3428", help="CCRG 服务地址")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3500)
    args = parser.parse_args()

    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stderr)])

    standalone_app = FastAPI(title="CCRG MCP Server (standalone)")
    register_routes(standalone_app, lambda: args.ccrg_url)

    config = uvicorn.Config(standalone_app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    logger.info(f"CCRG MCP Server (standalone) listening on http://{args.host}:{args.port}")
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
