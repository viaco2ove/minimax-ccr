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
import os
import logging
import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

logger = logging.getLogger("ccrg.mcp")


def _load_log_config() -> dict:
    """加载 log_config.json"""
    config_path = Path(__file__).parent.parent.parent.parent / "log_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


_log_config = _load_log_config()


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
    },
    {
        "name": "ccrg_code",
        "description": "编程 Agent 工具 — 服务端执行文件读写和命令，LLM 负责思考和生成代码。支持 read/write/exec/loop/plan/review/chat。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": ["read", "write", "exec", "loop", "plan", "review", "chat"],
                    "description": "read=读文件让LLM分析, write=LLM生成代码自动写入文件, exec=执行命令LLM分析结果, loop=自动循环修复直到成功, plan=规划任务, review=审查代码, chat=对话"
                },
                "task": {
                    "type": "string",
                    "description": "任务描述（必须）"
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "相关文件路径列表",
                    "default": []
                },
                "file_contents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["path", "content"]
                    },
                    "description": "文件内容（用于 write 操作）",
                    "default": []
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要执行的命令列表（用于 exec 操作）",
                    "default": []
                },
                "context": {
                    "type": "string",
                    "description": "额外上下文信息（如项目结构、错误日志等）",
                    "default": ""
                },
                "model": {
                    "type": "string",
                    "description": "指定模型（留空自动路由）",
                    "default": ""
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "loop 模式最大轮数",
                    "default": 500
                },
                "stream": {
                    "type": "boolean",
                    "description": "是否通过 SSE 流式返回每一步结果（默认 true）；false 则等待全部完成后再返回",
                    "default": True
                }
            },
            "required": ["task_type", "task"]
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


async def _handle_ccrg_code(args: dict, base_url: str) -> dict:
    """复杂编程工具 — 服务端执行文件读写/命令 + LLM 思考

    工作方式：
    - read:  服务端读文件 → 内容发给 LLM 分析 → 返回分析结果
    - write: 内容发给 LLM → LLM 输出修改建议 → 服务端写入文件
    - exec:  服务端执行命令 → stdout/stderr 发给 LLM 分析 → 返回分析+结果
    - loop:  exec→分析→write→exec 循环直到成功或达到 max_rounds
    - plan:  项目结构发给 LLM → 返回实现计划
    - review:文件内容发给 LLM → 返回审查建议
    """
    import os
    import subprocess
    import re as _re

    task_type = args.get("task_type", "chat")
    task = args.get("task", "")
    files = args.get("files", [])
    file_contents = args.get("file_contents", [])
    commands = args.get("commands", [])
    context = args.get("context", "")
    model = args.get("model", "")
    max_rounds = args.get("max_rounds", 500)
    stream = args.get("stream", True)

    if task_type == "read":
        return await _code_read(task, files, context, base_url, model, stream)

    elif task_type == "write":
        return await _code_write(task, files, file_contents, context, base_url, model, stream)

    elif task_type == "exec":
        return await _code_exec(task, commands, context, base_url, model, stream)

    elif task_type == "loop":
        return await _code_loop(task, commands, files, context, base_url, model, max_rounds, _emit_sse if stream else None)

    elif task_type == "plan":
        return await _code_plan(task, files, context, base_url, model, stream)

    elif task_type == "review":
        return await _code_review(task, files, file_contents, context, base_url, model, stream)

    else:
        system_prompt = "你是一个高级编程助手。"
        user_msg = f"{context}\n\n{task}" if context else task
        return await _call_llm(base_url, system_prompt, user_msg, model)


# ─── read: 读文件 → LLM 分析 ──────────────────────────────────

async def _code_read(task: str, files: list, context: str, base_url: str, model: str, stream: bool = True) -> dict:
    import os
    file_data = {}
    for fp in files:
        try:
            abs_path = os.path.abspath(fp)
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            file_data[fp] = content
            logger.info(f"[ccrg_code:read] read {fp} ({len(content)} chars)")
        except Exception as e:
            file_data[fp] = f"ERROR: {e}"
            logger.warning(f"[ccrg_code:read] failed to read {fp}: {e}")

    system_prompt = (
        "你是一个高级编程助手。用户提供了项目文件内容，请仔细分析并回答问题。\n"
        "回答要具体，引用文件中的行号和代码片段。"
    )
    user_msg = f"## 任务\n{task}\n\n"
    if context:
        user_msg += f"## 额外上下文\n{context}\n\n"
    user_msg += "## 项目文件\n"
    for fp, content in file_data.items():
        user_msg += f"\n### {fp}\n```\n{content[:15000]}\n```\n"

    if stream:
        result = await _call_llm_stream(base_url, system_prompt, user_msg, model, _emit_sse)
        result["files_read"] = list(file_data.keys())
        return result
    else:
        result = await _call_llm(base_url, system_prompt, user_msg, model)
        result["files_read"] = list(file_data.keys())
        return result


# ─── write: LLM 生成代码 → 服务端写入文件 ──────────────────────

async def _code_write(task: str, files: list, file_contents: list, context: str, base_url: str, model: str, stream: bool = True) -> dict:
    import os

    system_prompt = (
        "你是一个高级编程助手。请根据任务修改代码文件。\n\n"
        "输出格式要求：\n"
        "请输出一个 JSON 数组，每个元素包含 path 和 content 字段。\n"
        "```json\n[{\"path\": \"file.py\", \"content\": \"完整文件内容...\"}]\n```\n"
        "只输出 JSON，不要其他内容。确保每个文件输出完整内容（不是 diff）。"
    )

    user_msg = f"## 任务\n{task}\n\n"
    if context:
        user_msg += f"## 额外上下文\n{context}\n\n"
    if file_contents:
        user_msg += "## 要修改的文件\n"
        for fc in file_contents:
            user_msg += f"\n### {fc['path']}\n```\n{fc['content'][:15000]}\n```\n"
    elif files:
        for fp in files:
            try:
                abs_path = os.path.abspath(fp)
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
                user_msg += f"\n### {fp}\n```\n{content[:15000]}\n```\n"
            except Exception:
                pass

    if stream:
        result = await _call_llm_stream(base_url, system_prompt, user_msg, model, _emit_sse)
    else:
        result = await _call_llm(base_url, system_prompt, user_msg, model)

    # 解析 LLM 输出，写入文件
    if "text" in result:
        written_files = []
        try:
            text = result["text"]
            # 提取 JSON 数组（兼容 markdown 代码块包裹）
            json_match = _re.search(r'```(?:json)?\s*\n?(\[[\s\S]*?\])\s*\n?```', text)
            if json_match:
                code_blocks = json.loads(json_match.group(1))
            else:
                start = text.find("[")
                end = text.rfind("]") + 1
                code_blocks = json.loads(text[start:end])

            for block in code_blocks:
                fp = block.get("path", "")
                content = block.get("content", "")
                if fp and content:
                    abs_path = os.path.abspath(fp)
                    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    written_files.append(fp)
                    logger.info(f"[ccrg_code:write] wrote {fp} ({len(content)} chars)")
            result["written_files"] = written_files
        except json.JSONDecodeError as e:
            result["write_error"] = f"Failed to parse LLM output as JSON: {e}"
            logger.error(f"[ccrg_code:write] JSON parse error: {e}")
        except Exception as e:
            result["write_error"] = str(e)
            logger.error(f"[ccrg_code:write] error: {e}")

    return result


# ─── exec: 执行命令 → LLM 分析结果 ────────────────────────────

async def _code_exec(task: str, commands: list, context: str, base_url: str, model: str, stream: bool = True) -> dict:
    import subprocess

    results = {}
    for cmd in commands:
        try:
            logger.info(f"[ccrg_code:exec] running: {cmd}")
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace"
            )
            results[cmd] = {
                "stdout": proc.stdout[:10000],
                "stderr": proc.stderr[:5000],
                "returncode": proc.returncode
            }
            logger.info(f"[ccrg_code:exec] {cmd} → exit={proc.returncode}, stdout={len(proc.stdout)} chars")
        except subprocess.TimeoutExpired:
            results[cmd] = {"error": "timeout (120s)"}
            logger.warning(f"[ccrg_code:exec] {cmd} → timeout")
        except Exception as e:
            results[cmd] = {"error": str(e)}
            logger.warning(f"[ccrg_code:exec] {cmd} → error: {e}")

    # 让 LLM 分析执行结果
    system_prompt = (
        "你是一个高级编程助手。用户执行了命令，请分析执行结果。\n"
        "如果命令失败，分析错误原因并给出修复建议。\n"
        "如果命令成功，确认结果是否符合预期。"
    )
    user_msg = f"## 任务\n{task}\n\n"
    if context:
        user_msg += f"## 额外上下文\n{context}\n\n"
    user_msg += "## 命令执行结果\n"
    for cmd, res in results.items():
        user_msg += f"\n### `{cmd}`\n"
        if "error" in res:
            user_msg += f"错误: {res['error']}\n"
        else:
            user_msg += f"退出码: {res['returncode']}\n"
            if res["stdout"]:
                user_msg += f"stdout:\n```\n{res['stdout'][:3000]}\n```\n"
            if res["stderr"]:
                user_msg += f"stderr:\n```\n{res['stderr'][:2000]}\n```\n"

    if stream:
        analysis = await _call_llm_stream(base_url, system_prompt, user_msg, model, _emit_sse)
    else:
        analysis = await _call_llm(base_url, system_prompt, user_msg, model)
    analysis["command_results"] = results

    # 判断是否有失败
    has_failure = any(
        (r.get("returncode", 0) != 0 if "returncode" in r else True)
        for r in results.values()
    )
    analysis["has_failure"] = has_failure
    return analysis


# ─── loop: 流式循环（SSE 实时推送每一步）─────────────

async def _code_loop(task: str, commands: list, files: list, context: str,
                    base_url: str, model: str, max_rounds: int = 500,
                    _emit: callable = None) -> dict:
    """
    流式自动修复循环：exec → 分析 → write → exec ...
    每一步通过 SSE 实时推送，qoder 实时看到进度。

    Args:
        _emit: SSE 推送回调，_emit({"type": "step", "text": "...", "done": False})
    """
    import subprocess
    import re as _re

    accumulated_context = context or ""
    all_steps = []

    def emit(text: str, done: bool = False):
        if _emit:
            _emit({"type": "step", "text": text, "done": done})
        all_steps.append(text)

    for round_num in range(1, max_rounds + 1):
        round_header = f"## 🔄 Round {round_num}/{max_rounds}"
        emit(round_header)

        # ── Step 1: 执行命令 ──────────────────────────────────
        emit(f"\n**Step 1: 执行命令**\n")
        exec_results = {}
        all_success = True
        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=120, encoding="utf-8", errors="replace"
                )
                exec_results[cmd] = {
                    "stdout": proc.stdout[:10000],
                    "stderr": proc.stderr[:5000],
                    "returncode": proc.returncode
                }
                if proc.returncode != 0:
                    all_success = False
            except Exception as e:
                exec_results[cmd] = {"error": str(e)}
                all_success = False

        for cmd, res in exec_results.items():
            rc = res.get("returncode", "?")
            status = "✅ 成功" if rc == 0 else f"❌ 失败 (退出码: {rc})"
            emit(f"`{cmd}` → {status}")
            err = res.get("stderr", "") or res.get("error", "")
            if err:
                emit(f"```\n{err[:1500]}\n```")

        # ── Step 2: 成功则返回 ────────────────────────────────
        if all_success:
            emit(f"\n**任务完成！**")
            system_prompt = "任务已完成。请简要总结执行结果。"
            user_msg = f"## 任务\n{task}\n\n## 执行结果\n"
            for cmd, res in exec_results.items():
                user_msg += f"`{cmd}` → 退出码: {res.get('returncode', '?')}\n"
                if res.get("stdout"):
                    user_msg += f"```\n{res['stdout'][:1000]}\n```\n"
            result = await _call_llm_stream(base_url, system_prompt, user_msg, model, emit)
            emit(f"\n**LLM 总结:**\n{result.get('text', '')[:2000]}")
            emit("", done=True)
            return {"success": True, "text": "\n".join(all_steps)}

        # ── Step 3: 读取文件 ─────────────────────────────────
        emit(f"\n**Step 2: 读取相关文件**\n")
        file_context = ""
        for fp in files:
            try:
                abs_path = os.path.abspath(fp)
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    file_context += f"\n### {fp}\n```\n{content[:15000]}\n```\n"
                emit(f"- `{fp}` ({len(content)} chars)")
            except Exception as e:
                emit(f"- `{fp}` (读取失败: {e})")

        # ── Step 4: LLM 流式分析 + 生成修复 ────────────────
        emit(f"\n**Step 3: 分析错误 & 生成修复代码**\n")
        system_prompt = (
            "你是一个高级编程助手。程序执行失败了，请分析错误并输出修复后的完整文件代码。\n\n"
            "输出格式：一个 JSON 数组，每个元素包含 path 和 content 字段。\n"
            "```json\n[{\"path\": \"file.py\", \"content\": \"完整修复后的文件内容...\"}]\n```\n"
            "只输出 JSON，不要其他内容。"
        )
        user_msg = f"## 任务\n{task}\n\n## 执行失败\n"
        user_msg += "\n".join([res.get("stderr", "") or res.get("error", "") for res in exec_results.values()])
        if file_context:
            user_msg += f"\n\n## 相关文件内容\n{file_context}"
        if accumulated_context:
            user_msg += f"\n\n## 历史上下文\n{accumulated_context}"

        fix_result = await _call_llm_stream(base_url, system_prompt, user_msg, model, emit)
        emit(f"\n**LLM 分析:**\n{fix_result.get('text', '')[:3000]}")

        # ── Step 5: 写入修复文件 ────────────────────────────
        emit(f"\n**Step 4: 写入修复文件**\n")
        if "text" in fix_result:
            try:
                text = fix_result["text"]
                json_match = _re.search(r'```(?:json)?\s*\n?(\[[\s\S]*?\])\s*\n?```', text)
                if json_match:
                    code_blocks = json.loads(json_match.group(1))
                else:
                    start = text.find("[")
                    end = text.rfind("]") + 1
                    code_blocks = json.loads(text[start:end])

                import os as _os
                for block in code_blocks:
                    fp = block.get("path", "")
                    content = block.get("content", "")
                    if fp and content:
                        abs_path = _os.path.abspath(fp)
                        _os.makedirs(_os.path.dirname(abs_path) or ".", exist_ok=True)
                        with open(abs_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        emit(f"- ✅ 已写入: `{fp}` ({len(content)} chars)")
                        logger.info(f"[ccrg_code:loop] wrote {fp}")
            except Exception as e:
                emit(f"- ❌ 写入失败: {e}")
                logger.error(f"[ccrg_code:loop] write error: {e}")

        accumulated_context += f"\n\n### Round {round_num}\n" + "\n".join(all_steps[-20:])

    emit(f"\n⚠️ 达到最大轮数 ({max_rounds})，停止", done=True)
    return {"success": False, "text": "\n".join(all_steps)}


# ─── 流式 LLM 调用 ──────────────────────────────────────────────

async def _call_llm_stream(base_url: str, system: str, user_msg: str,
                           model: str = "", emit: callable = None) -> dict:
    """流式调用 LLM，每个 token 都通过 SSE 推送"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg}
    ]
    body: dict[str, Any] = {"messages": messages, "stream": True}
    if model:
        body["model"] = model

    full_text = ""
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", f"{base_url}/v1/chat/completions",
            json=body, headers={"Content-Type": "application/json"}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                except Exception:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    full_text += delta
                    if emit:
                        emit(delta, done=False)

    if emit:
        emit("\n", done=False)
    return {"text": full_text, "model": body.get("model", "")}


# ─── plan: 规划任务 ───────────────────────────────────────────

async def _code_plan(task: str, files: list, context: str, base_url: str, model: str, stream: bool = True) -> dict:
    system_prompt = (
        "你是一个高级编程架构师。请根据用户需求制定详细的实现计划。\n"
        "输出格式：\n"
        "1. 需求分析\n"
        "2. 技术方案（包括要修改/新建的文件）\n"
        "3. 实现步骤（每步具体操作）\n"
        "4. 测试验证\n"
        "请用中文回答，步骤要具体可执行。"
    )
    user_msg = f"## 任务\n{task}\n\n"
    if context:
        user_msg += f"## 额外上下文\n{context}\n\n"
    if files:
        user_msg += "## 相关文件\n" + "\n".join(f"- `{f}`" for f in files) + "\n"
    user_msg += "\n请制定详细的实现计划。"
    if stream:
        return await _call_llm_stream(base_url, system_prompt, user_msg, model, _emit_sse)
    else:
        return await _call_llm(base_url, system_prompt, user_msg, model)


# ─── review: 审查代码 ─────────────────────────────────────────

async def _code_review(task: str, files: list, file_contents: list, context: str, base_url: str, model: str, stream: bool = True) -> dict:
    system_prompt = (
        "你是一个高级代码审查专家。请审查代码并给出改进建议。\n"
        "关注：正确性、安全性、性能、可维护性。\n"
        "按严重程度排序：P0（阻断）→ P1（重要）→ P2（建议）。"
    )
    user_msg = f"## 审查任务\n{task}\n\n"
    if context:
        user_msg += f"## 额外上下文\n{context}\n\n"
    for fc in file_contents:
        user_msg += f"\n### {fc['path']}\n```\n{fc['content'][:15000]}\n```\n"
    if not file_contents and files:
        for fp in files:
            try:
                abs_path = os.path.abspath(fp)
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
                user_msg += f"\n### {fp}\n```\n{content[:15000]}\n```\n"
            except Exception:
                pass
    if stream:
        return await _call_llm_stream(base_url, system_prompt, user_msg, model, _emit_sse)
    else:
        return await _call_llm(base_url, system_prompt, user_msg, model)


async def _call_llm(base_url: str, system: str, user_msg: str, model: str = "") -> dict:
    """通过 CCRG 的 /v1/chat/completions 调用 LLM"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg}
    ]
    body: dict[str, Any] = {
        "messages": messages,
        "stream": False
    }
    if model:
        body["model"] = model

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json=body,
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        result = resp.json()

    choices = result.get("choices", [])
    text = choices[0].get("message", {}).get("content", "") if choices else ""
    return {
        "text": text,
        "model": result.get("model", ""),
        "usage": result.get("usage", {})
    }


# ─── 工具分发 ───────────────────────────────────────────────────

_HANDLERS = {
    "ccrg_chat": _handle_ccrg_chat,
    "ccrg_route": _handle_ccrg_route,
    "ccrg_stats": _handle_ccrg_stats,
    "ccrg_health": _handle_ccrg_health,
    "ccrg_code": _handle_ccrg_code,
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

# 用于在 handler 调用链中传递当前 session 的 SSE 队列（避免改函数签名）
_current_session_queue: asyncio.Queue | None = None


def _emit_sse(event: dict):
    """向当前 SSE 会话推送一个事件（由 _code_loop 等调用）"""
    global _current_session_queue
    if _current_session_queue:
        try:
            _current_session_queue.put_nowait(event)
        except Exception:
            pass


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
        global _current_session_queue

        # 宽松 JSON 解析：允许 trailing comma
        raw_body = await request.body()
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as e:
            if "trailing comma" in str(e) or "Extra data" in str(e):
                # 移除 trailing comma 后重试
                import re
                fixed = re.sub(r',(\s*[}\]])', r'\1', raw_body.decode('utf-8', errors='replace'))
                try:
                    body = json.loads(fixed)
                except json.JSONDecodeError:
                    return JSONResponse(status_code=400, content={"error": f"Invalid JSON: {e}"})
            else:
                return JSONResponse(status_code=400, content={"error": f"Invalid JSON: {e}"})
        method = body.get("method", "")
        req_id = body.get("id")

        if method == "ping":
            if _log_config.get("MCP_PING_LOG", False):
                logger.info(f"[MCP-SSE] method=ping, id={req_id}, session={sessionId[:8]}...")
            return JSONResponse(content={"jsonrpc": "2.0", "id": req_id, "result": {}})

        if method == "tools/call":
            tool_name = body.get("params", {}).get("name", "")
            tool_args = body.get("params", {}).get("arguments", {})
            task_type = tool_args.get("task_type", "")
            is_loop = (tool_name == "ccrg_code" and task_type == "loop")

            logger.info(f"[MCP-SSE] method={method}, id={req_id}, tool={tool_name}, session={sessionId[:8]}...")
            logger.info(f"[MCP-SSE] tool_call: {tool_name} args={json.dumps(tool_args, ensure_ascii=False)[:200]}")

            # 所有 ccrg_code 工具都支持流式：设置 SSE 队列
            should_stream = tool_args.get("stream", True)
            if should_stream and tool_name == "ccrg_code" and sessionId and sessionId in _sse_sessions:
                _current_session_queue = _sse_sessions[sessionId]
                logger.info(f"[MCP-SSE] stream=true: SSE queue set for session {sessionId[:8]}...")
                try:
                    response = await handle_jsonrpc(body, base_url_provider())
                finally:
                    _current_session_queue = None
                return JSONResponse(content={"status": "accepted"})
            elif should_stream and tool_name == "ccrg_code":
                # 无 session 时，直接返回 SSE 流
                logger.info(f"[MCP-SSE] stream=true: inline SSE (no session)")

                async def sse_stream():
                    global _current_session_queue
                    q: asyncio.Queue = asyncio.Queue()
                    _current_session_queue = q

                    async def run():
                        try:
                            await handle_jsonrpc(body, base_url_provider())
                        finally:
                            q.put_nowait(None)  # 结束信号

                    t = asyncio.create_task(run())
                    while True:
                        item = await q.get()
                        if item is None:
                            break
                        yield f"event: message\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                    await t
                    _current_session_queue = None

                return StreamingResponse(sse_stream(), media_type="text/event-stream")
            else:
                _current_session_queue = None
                try:
                    response = await handle_jsonrpc(body, base_url_provider())
                finally:
                    _current_session_queue = None
                if response is not None:
                    if sessionId and sessionId in _sse_sessions:
                        await _sse_sessions[sessionId].put(response)
                        return JSONResponse(content={"status": "accepted"})
                    return JSONResponse(content=response)
                return JSONResponse(content={})
        else:
            logger.info(f"[MCP-SSE] method={method}, id={req_id}, session={sessionId[:8]}...")
            response = await handle_jsonrpc(body, base_url_provider())
            if sessionId and sessionId in _sse_sessions:
                if response is not None:
                    await _sse_sessions[sessionId].put(response)
                return JSONResponse(content={"status": "accepted"})
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
