"""
LLMSplitter — 基于 LLM 模型的工作流意图分流器。

通过配置的 provider:model 调用外部 LLM，根据用户输入判断是 chat 还是 task 模式。
"""

import json
import logging
from typing import Any, Literal

import httpx

from .base import Splitter

logger = logging.getLogger("ccrg")


class LLMSplitter(Splitter):
    """使用 LLM 模型判断工作流意图：chat 或 task"""

    DEFAULT_SYSTEM_PROMPT = (
        "你是一个工作流意图分类器。根据用户消息判断是 'chat'（闲聊问答）还是 'task'（任务执行）模式。\n"
        "回复格式：只输出一个词 'chat' 或 'task'，不要其他内容。"
    )

    DEFAULT_USER_TEMPLATE = "用户消息：{text}\n意图："

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None):
        self.config = config or {}
        self.keywords = keywords
        self.registry = registry

        splitter_cfg = self.config.get("routing", {}).get("splitter", {})
        llm_cfg = splitter_cfg.get("llm_splitter", {})

        # llm_splitter 配置：provider:model 列表
        self.routes: list[str] = llm_cfg.get("routes", ["minimax:MiniMax-M2.7"])
        self.system_prompt = llm_cfg.get("system_prompt", self.DEFAULT_SYSTEM_PROMPT)
        self.user_template = llm_cfg.get("user_template", self.DEFAULT_USER_TEMPLATE)
        self.timeout = llm_cfg.get("timeout", 10.0)

        self.fallback_splitter: Splitter | None = None

        logger.info(f"LLMSplitter configured: routes={self.routes}")

    def detect_intent(self, body: dict) -> Literal["chat", "task"]:
        """使用 LLM 判断意图"""
        user_text = self._extract_user_text(body)
        if not user_text.strip():
            logger.debug("LLMSplitter: empty user text, using keyword fallback")
            return self._keyword_fallback(body)

        # 尝试所有配置的 routes
        last_error = None
        for route in self.routes:
            try:
                intent = self._call_llm(route, user_text)
                if intent in ("chat", "task"):
                    logger.info(f"LLMSplitter matched intent={intent} via {route}")
                    return intent
                logger.warning(f"LLMSplitter {route} returned invalid intent: {intent!r}")
            except Exception as e:
                last_error = e
                logger.warning(f"LLMSplitter {route} failed: {e}")
                continue

        logger.warning(f"LLMSplitter all routes failed, using keyword fallback")
        return self._keyword_fallback(body)

    def _call_llm(self, route: str, user_text: str) -> str:
        """调用单个 LLM route"""
        if ":" not in route:
            raise ValueError(f"Invalid route format: {route}")

        provider, model = route.split(":", 1)
        prov_config = self.registry.get(provider) if self.registry else None

        if not prov_config:
            # 尝试直接从 config 构建请求
            return self._call_direct(provider, model, user_text)

        return self._call_via_registry(provider, model, user_text, prov_config)

    def _call_via_registry(
        self, provider: str, model: str, user_text: str, prov_config: Any
    ) -> str:
        """通过 registry 获取 provider 配置后调用"""
        # 复用 main.py 中的 adapter 选择逻辑
        adapter_name = getattr(prov_config, "providers_adapter", "") or getattr(prov_config, "protocol", "")

        # 构建请求
        req_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": self.system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": self.user_template.format(text=user_text)}]},
            ],
            "max_tokens": 10,
            "stream": False,
        }

        api_base = getattr(prov_config, "api_base_url", "")
        api_key = getattr(prov_config, "api_key", "")

        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{api_base.rstrip('/')}/messages", json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text", "").strip().lower()
                # 取第一行（去掉可能的解释文字）
                first_line = text.split("\n")[0].strip()
                if first_line in ("chat", "task"):
                    return first_line
                return first_line
        return ""

    def _call_direct(self, provider: str, model: str, user_text: str) -> str:
        """直接调用（不依赖 registry），从 config 中查找 provider"""
        # 尝试从 self.config 中找 provider 配置
        providers = self.config.get("providers", {})
        prov_data = providers.get(provider)
        if not prov_data:
            raise ValueError(f"Provider {provider} not found in config")

        api_base = prov_data.get("api_base_url", "")
        api_key = prov_data.get("api_key", "")

        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        # 假设是 anthropic 兼容协议
        req_body = {
            "model": model,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": self.system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": self.user_template.format(text=user_text)}]},
            ],
            "max_tokens": 10,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{api_base.rstrip('/')}/messages", json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("content", [])
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text", "").strip().lower()
                first_line = text.split("\n")[0].strip()
                if first_line in ("chat", "task"):
                    return first_line
                return first_line
        return ""

    def _keyword_fallback(self, body: dict) -> Literal["chat", "task"]:
        """当 LLM 判断失败时，回退到 keyword splitter"""
        if self.fallback_splitter is None:
            from .keyword import KeywordSplitter

            self.fallback_splitter = KeywordSplitter(
                config=self.config,
                keywords=self.keywords,
                registry=self.registry,
            )
        return self.fallback_splitter.detect_intent(body)

    def _extract_user_text(self, body: dict) -> str:
        """提取用户消息文本"""
        texts = []
        for msg in body.get("messages", []):
            role = msg.get("role", "")
            if role != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
        return " ".join(texts)