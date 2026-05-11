"""
MiniMax Local Provider for Claude Code Router

启动后监听 3457 端口，接收 CCR 请求，调用 mmx CLI，返回响应。
支持 OpenAI Chat Completions 格式（/v1/chat/completions）和 Anthropic 格式（/v1/messages）。
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "logs/mmx_provider.log")

# 确保日志目录存在
os.makedirs(os.path.dirname(LOG_FILE) if os.path.dirname(LOG_FILE) else ".", exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("mmx_provider")

PORT = int(os.getenv("PORT", 3457))
HOST = os.getenv("HOST", "127.0.0.1")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "MiniMax-M2.7")
TIMEOUT = int(os.getenv("TIMEOUT", 300))
MMX_CLI = os.getenv("MMX_CLI", "mmx")
NODE_EXE = os.getenv("NODE_EXE", "node")
SYSTEM_PROMPT = "你是一个友好的AI助手。用户会发送消息，请简洁明了地回复。"
MAX_SYSTEM_PROMPT_LENGTH = int(os.getenv("MAX_SYSTEM_PROMPT_LENGTH", 8000))


def _find_mmx_dir():
    import shutil
    exe = shutil.which(MMX_CLI)
    if exe:
        return os.path.dirname(os.path.abspath(exe))
    return os.path.dirname(os.path.abspath(__file__))

MMX_CLI_DIR = _find_mmx_dir()
MMX_SCRIPT = os.path.join(MMX_CLI_DIR, "node_modules", "mmx-cli", "dist", "mmx.mjs")


# ── 共享：mmx CLI 调用 ──────────────────────────────────────────────

def call_mmx(system_prompt, clean_messages, model):
    """调用 mmx CLI，返回 (response_text, thinking_text, error)"""
    messages_json = json.dumps(clean_messages, ensure_ascii=False)
    logger.info(f"Messages: clean={len(clean_messages)}, total chars: {len(messages_json)}")
    logger.debug(f"clean_messages JSON: {messages_json}")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', encoding='utf-8', delete=False
        ) as f:
            f.write(messages_json)
            tmp_path = f.name

        cmd = [
            NODE_EXE, MMX_SCRIPT,
            "text", "chat",
            "--model", model,
            "--system", system_prompt,
            "--messages-file", tmp_path
        ]
        logger.info(f"Calling: {NODE_EXE} {MMX_SCRIPT} text chat --model {model} "
                     f"--system [len={len(system_prompt)}] --messages-file {tmp_path}")

        call_start = time.time()
        result = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)

        raw_output = result.stdout.decode("utf-8", errors="replace").strip()
        stderr_output = result.stderr.decode("utf-8", errors="replace").strip()
        if stderr_output:
            logger.warning(f"mmx stderr: {stderr_output[:500]}")

        logger.info(f"[RESPONSE @{time.time():.3f}] raw_len={len(raw_output)}, "
                     f"call_duration={time.time()-call_start:.3f}s")
        logger.debug(f"  raw stdout (first 300): {raw_output[:300]}")

        if not raw_output:
            return None, None, "mmx returned empty output"

        try:
            mmx_response = json.loads(raw_output)
            content_blocks = mmx_response.get("content", [])
            text_parts = []
            thinking_parts = []
            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get("thinking"):
                        thinking_parts.append(block["thinking"])
                    if block.get("text"):
                        text_parts.append(block["text"])
            response_text = "\n".join(text_parts) if text_parts else None
            thinking_text = "\n".join(thinking_parts) if thinking_parts else None
            if not response_text and not thinking_text:
                logger.warning("MiniMax response has no text/thinking, falling back to raw output")
                response_text = raw_output
            return response_text, thinking_text, None
        except json.JSONDecodeError:
            return raw_output, None, None

    except subprocess.TimeoutExpired:
        return None, None, "mmx command timed out"
    except Exception as e:
        return None, None, str(e)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def strip_system_reminder(text):
    """去掉 system-reminder 块"""
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL).strip()


def truncate_system_prompt(prompt):
    if len(prompt) > MAX_SYSTEM_PROMPT_LENGTH:
        logger.warning(f"system prompt too long ({len(prompt)} chars), truncating to {MAX_SYSTEM_PROMPT_LENGTH}")
        return prompt[:MAX_SYSTEM_PROMPT_LENGTH]
    return prompt


# ── 共享：HTTP 响应发送 ──────────────────────────────────────────────

def send_response_body(handler, body_bytes):
    """写 Content-Length + end_headers + body。调用前须已完成 send_response/send_header"""
    handler.send_header("Content-Length", str(len(body_bytes)))
    handler.end_headers()
    try:
        handler.wfile.write(body_bytes)
        handler.wfile.flush()
    except BrokenPipeError:
        logger.warning("Client disconnected during response")
    except Exception as e:
        logger.error(f"Response write error: {e}")


# ── OpenAI Chat Completions 格式 ──────────────────────────────────────

class OAIHandler:
    """OpenAI Chat Completions 格式的请求处理和响应构建"""

    @staticmethod
    def parse_request(request):
        """解析 OpenAI Chat Completions 请求，返回 (system_prompt, clean_messages, model, stream)"""
        messages = request.get("messages", [])
        model = request.get("model", DEFAULT_MODEL)
        stream = request.get("stream", False)

        logger.info(f"OAI request: model={model}, stream={stream}, messages={len(messages)}")

        # 从 messages 中提取 system
        system_prompt = SYSTEM_PROMPT
        remaining = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_prompt = content.strip()
                continue
            remaining.append(msg)

        system_prompt = strip_system_reminder(system_prompt)
        system_prompt = truncate_system_prompt(system_prompt)

        # 转换消息为 mmx 能理解的简单文本格式
        clean_messages = []
        for msg in remaining:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            # content 可能是 string 或 list
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                        elif item.get("type") == "image_url":
                            text_parts.append("[image]")
                        else:
                            text_parts.append(json.dumps(item, ensure_ascii=False))
                    else:
                        text_parts.append(str(item))
                content = " ".join(text_parts)
            elif content is None:
                content = ""
            else:
                content = str(content)

            content = strip_system_reminder(content)

            # tool 角色转成 user 角色文本
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "unknown")
                content = f"[Tool result for {tool_call_id}]: {content}"
                role = "user"

            # assistant 的 tool_calls 转成文本
            if role == "assistant" and tool_calls:
                tc_parts = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tc_parts.append(f"{fn.get('name', 'unknown')}({fn.get('arguments', '{}')})")
                if content:
                    content += f"\n[Calling tools: {', '.join(tc_parts)}]"
                else:
                    content = f"[Calling tools: {', '.join(tc_parts)}]"

            if not content:
                content = "[empty]"

            clean_messages.append({"role": role, "content": content})

        return system_prompt, clean_messages, model, stream

    @staticmethod
    def build_json_response(response_text, thinking_text, model, error=None):
        """构建 OpenAI Chat Completions 非流式 JSON 响应"""
        if error:
            return {
                "error": {
                    "message": error,
                    "type": "server_error",
                    "code": 500
                }
            }

        message = {"role": "assistant", "content": response_text or ""}
        if thinking_text:
            message["reasoning_content"] = thinking_text

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }
        }

    @staticmethod
    def build_sse_chunks(response_text, thinking_text, model):
        """构建 OpenAI Chat Completions 流式 SSE 数据块列表"""
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        def make_chunk(delta, finish_reason=None):
            chunk = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason
                }]
            }
            return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

        chunks = []

        # 首个 chunk：角色
        chunks.append(make_chunk({"role": "assistant"}))

        # thinking 作为 reasoning_content
        if thinking_text:
            for i in range(0, len(thinking_text), 40):
                chunks.append(make_chunk({"reasoning_content": thinking_text[i:i+40]}))

        # content
        if response_text:
            for i in range(0, len(response_text), 40):
                chunks.append(make_chunk({"content": response_text[i:i+40]}))

        # 结束
        chunks.append(make_chunk({}, "stop"))
        chunks.append(b"data: [DONE]\n\n")

        return chunks


# ── Anthropic Messages 格式（向后兼容） ──────────────────────────────────

class AnthropicHandler:
    """Anthropic Messages 格式的请求处理和响应构建"""

    @staticmethod
    def parse_request(request):
        """解析 Anthropic Messages 请求，返回 (system_prompt, clean_messages, model, stream)"""
        messages = request.get("messages", [])
        model = request.get("model", DEFAULT_MODEL)
        stream = request.get("stream", False)

        logger.info(f"Anthropic request: model={model}, stream={stream}, messages={len(messages)}")

        # 提取 system prompt
        raw_system = request.get("system")
        if isinstance(raw_system, str) and raw_system.strip():
            system_prompt = raw_system.strip()
        elif isinstance(raw_system, list):
            parts = []
            for item in raw_system:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            system_prompt = " ".join(parts) or SYSTEM_PROMPT
        else:
            system_prompt = SYSTEM_PROMPT

        system_prompt = strip_system_reminder(system_prompt)
        system_prompt = truncate_system_prompt(system_prompt)

        clean_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        item_type = item.get("type", "")
                        if item_type == "text":
                            text_parts.append(item.get("text", ""))
                        elif item_type == "image":
                            text_parts.append("[image]")
                        elif item_type == "tool_use":
                            text_parts.append(f"[tool: {item.get('name', 'unknown')}]")
                        else:
                            text_parts.append(json.dumps(item, ensure_ascii=False))
                    else:
                        text_parts.append(str(item))
                content = " ".join(text_parts)
            elif isinstance(content, dict):
                content = content.get("text", "") or json.dumps(content, ensure_ascii=False)
            else:
                content = str(content) if content else ""

            content = strip_system_reminder(content)

            if not content:
                content = "[empty]"

            if role == "assistant" and content.startswith("Error: mmx"):
                continue
            if role == "assistant" and not content.strip():
                content = "[assistant]"

            if role == "system" and system_prompt == SYSTEM_PROMPT:
                system_prompt = content
                continue

            clean_messages.append({"role": role, "content": content})

        return system_prompt, clean_messages, model, stream

    @staticmethod
    def build_json_response(response_text, model):
        """构建 Anthropic Messages 非流式 JSON 响应"""
        return {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": response_text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        }

    @staticmethod
    def build_sse_chunks(response_text, model):
        """构建 Anthropic Messages 流式 SSE 数据块列表"""
        response_id = f"msg_{uuid.uuid4().hex[:12]}"
        chunks = []

        chunks.append(f': {response_id}\n\n'.encode('utf-8'))

        msg_start = {
            "type": "message_start",
            "message": {
                "id": response_id, "type": "message", "role": "assistant",
                "model": model, "content": [], "stop_reason": None,
                "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}
            }
        }
        chunks.append(f'data: {json.dumps(msg_start)}\n\n'.encode('utf-8'))

        content_block = {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}
        }
        chunks.append(f'data: {json.dumps(content_block)}\n\n'.encode('utf-8'))

        for i in range(0, len(response_text), 20):
            chunk = response_text[i:i+20]
            delta = {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": chunk}}
            chunks.append(f'data: {json.dumps(delta)}\n\n'.encode('utf-8'))

        content_stop = {"type": "content_block_stop", "index": 0}
        chunks.append(f'data: {json.dumps(content_stop)}\n\n'.encode('utf-8'))

        msg_stop = {
            "type": "message_stop", "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}
        }
        chunks.append(f'data: {json.dumps(msg_stop)}\n\n'.encode('utf-8'))

        return chunks


# ── HTTP Server ──────────────────────────────────────────────────────

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class MMXHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"HTTP: {args[0]}")

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path in ["/v1/chat/completions", "/v1/chat/completions/"]:
            self._handle_oai()
        elif parsed.path in ["/", "/v1/messages", "/v1/messages/"] or parsed.path.endswith("/v1/messages"):
            self._handle_anthropic()
        else:
            logger.warning(f"Unknown path: {parsed.path}")
            self.send_error(404, "Not Found")

    def _read_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def _handle_oai(self):
        """处理 OpenAI Chat Completions 格式请求"""
        try:
            request = self._read_body()
        except (json.JSONDecodeError, Exception):
            self.send_error(400, "Invalid JSON")
            return

        system_prompt, clean_messages, model, stream = OAIHandler.parse_request(request)

        response_text, thinking_text, error = call_mmx(system_prompt, clean_messages, model)

        logger.info(f"OAI response: text_len={len(response_text or '')}, "
                     f"thinking_len={len(thinking_text or '')}, error={error}, stream={stream}")

        if stream:
            if error:
                # 流式模式下的错误：发一个带 error 的 chunk
                err_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": f"Error: {error}"}, "finish_reason": "stop"}]
                }
                body = f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                send_response_body(self, body)
            else:
                chunks = OAIHandler.build_sse_chunks(response_text, thinking_text, model)
                full_body = b''.join(chunks)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                send_response_body(self, full_body)
                logger.info(f"OAI streaming completed, {len(chunks)} chunks sent")
        else:
            resp = OAIHandler.build_json_response(response_text, thinking_text, model, error)
            status = 500 if error else 200
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            send_response_body(self, body)

    def _handle_anthropic(self):
        """处理 Anthropic Messages 格式请求（向后兼容）"""
        try:
            request = self._read_body()
        except (json.JSONDecodeError, Exception):
            self.send_error(400, "Invalid JSON")
            return

        system_prompt, clean_messages, model, stream = AnthropicHandler.parse_request(request)

        response_text, thinking_text, error = call_mmx(system_prompt, clean_messages, model)

        # Anthropic 格式忽略 thinking（旧格式不支持）
        if error:
            response_text = f"Error: {error}"
        elif not response_text:
            response_text = thinking_text or ""

        logger.info(f"Anthropic response: text_len={len(response_text)}, stream={stream}")

        if stream:
            chunks = AnthropicHandler.build_sse_chunks(response_text, model)
            full_body = b''.join(chunks)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            send_response_body(self, full_body)
            logger.info(f"Anthropic streaming completed, {len(chunks)} chunks sent")
        else:
            resp = AnthropicHandler.build_json_response(response_text, model)
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            send_response_body(self, body)


def main():
    logger.info(f"Starting MMX provider on http://{HOST}:{PORT}")
    logger.info(f"Endpoints: /v1/chat/completions (OpenAI), /v1/messages (Anthropic)")
    server = ThreadingHTTPServer((HOST, PORT), MMXHandler)
    logger.info("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MMX] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
