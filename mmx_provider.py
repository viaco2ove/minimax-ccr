"""
MiniMax Local Provider for Claude Code Router

启动后监听 3457 端口，接收 CCR 请求，调用 mmx CLI，返回响应。
"""

import json
import logging
import os
import subprocess
import uuid
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
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
TIMEOUT = int(os.getenv("TIMEOUT", 120))
MMX_CLI = os.getenv("MMX_CLI", "mmx")


class MMXHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"HTTP: {args[0]}")

    def do_POST(self):
        parsed = urlparse(self.path)

        # 支持多种路径格式
        if parsed.path in ["/", "/v1/messages", "/v1/chat/completions", "/v1/messages/"] or parsed.path.endswith("/v1/messages"):
            self.handle_messages()
        else:
            logger.warning(f"Unknown path: {parsed.path}")
            self.send_error(404, "Not Found")

    def handle_messages(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        # 提取消息
        messages = request.get("messages", [])
        model = request.get("model", DEFAULT_MODEL)
        stream = request.get("stream", False)

        # 构建 mmx 命令
        full_prompt = self.build_prompt(messages)
        cmd = [MMX_CLI, "text", "chat", "--model", model, "--message", full_prompt]

        logger.info(f"Calling command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                encoding="utf-8"
            )
            raw_output = result.stdout.strip()
            if result.stderr:
                logger.warning(f"mmx stderr: {result.stderr}")
            logger.debug(f"Raw output: {raw_output[:200]}...")

            # 尝试解析 mmx 输出为 JSON
            try:
                mmx_response = json.loads(raw_output)
                response_text = mmx_response.get("content", "")
                if isinstance(response_text, list):
                    # 提取纯文本，去掉 thinking 等
                    text_parts = []
                    for block in response_text:
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                    response_text = "\n".join(text_parts)
            except json.JSONDecodeError:
                response_text = raw_output

        except subprocess.TimeoutExpired:
            response_text = "Error: mmx command timed out"
            logger.error("mmx command timed out")
        except Exception as e:
            response_text = f"Error: {str(e)}"
            logger.error(f"mmx error: {str(e)}")

        # 构建 OpenAI 格式响应
        response = {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{
                "type": "text",
                "text": response_text
            }],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0
            }
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def build_prompt(self, messages):
        """将 messages 构建成 prompt 字符串"""
        import re
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, list):
                # 处理多模态内容
                text_parts = []
                for item in content:
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                content = " ".join(text_parts)
            elif isinstance(content, dict):
                content = content.get("text", "")

            # 过滤掉 system-reminder 块
            content = re.sub(r'<system-reminder>.*?</system-reminder>', '', content, flags=re.DOTALL)
            content = content.strip()

            # 跳过空内容
            if not content:
                continue

            # 跳过包含 Error 的 assistant 消息（避免错误信息回流）
            if role == "assistant" and "Error:" in content:
                continue

            if role == "user":
                parts.append(f"User: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            elif role == "system":
                parts.append(f"System: {content}")

        return "\n".join(parts)


def main():
    logger.info(f"Starting MMX provider on http://{HOST}:{PORT}")
    logger.info(f"Endpoint: http://{HOST}:{PORT}/v1/messages")
    server = HTTPServer((HOST, PORT), MMXHandler)
    logger.info("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MMX] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
