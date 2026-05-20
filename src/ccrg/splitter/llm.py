"""
LLMSplitter — 基于 LLM 模型的工作流意图分流器。

通过配置的 provider:model 调用外部 LLM，根据用户输入判断意图并返回路由。
取代 keyword_routing。
"""

import logging
from typing import Any

import httpx

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class LLMSplitter(Splitter):
    """使用 LLM 模型判断工作流意图并返回路由 — 取代 keyword_routing"""

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

        if isinstance(llm_cfg, list):
            self.routes: list[str] = llm_cfg
        else:
            self.routes: list[str] = llm_cfg.get("routes", ["minimax:MiniMax-M2.7"])
        self.system_prompt = llm_cfg.get("system_prompt", self.DEFAULT_SYSTEM_PROMPT) if isinstance(llm_cfg, dict) else self.DEFAULT_SYSTEM_PROMPT
        self.user_template = llm_cfg.get("user_template", self.DEFAULT_USER_TEMPLATE) if isinstance(llm_cfg, dict) else self.DEFAULT_USER_TEMPLATE
        self.timeout = llm_cfg.get("timeout", 10.0) if isinstance(llm_cfg, dict) else 10.0

        self.fallback_splitter: Splitter | None = None

        logger.info(f"LLMSplitter configured: routes={self.routes}")

    def detect(self, body: dict) -> RoutingDecision:
        """使用 LLM 判断意图并返回完整路由决策"""
        user_text = self._extract_user_text(body)
        if not user_text.strip():
            return self._keyword_fallback(body)

        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        for route in self.routes:
            try:
                intent = self._call_llm(route, user_text)
                if intent in ("chat", "task"):
                    logger.info(f"LLMSplitter matched intent={intent} via {route}")
                    route_str, fb = self._resolve_route(intent, rules)
                    return RoutingDecision(
                        intent=intent,
                        route=route_str,
                        matched_rule="llm_routing",
                        matched_reason=f"route={route}",
                        fallback=fb,
                    )
                logger.debug(f"LLMSplitter {route} returned invalid intent: {intent!r}")
            except Exception as e:
                logger.debug(f"LLMSplitter {route} failed: {e}")
                continue

        logger.debug(f"LLMSplitter all routes failed, using keyword fallback")
        return self._keyword_fallback(body)

    def _call_llm(self, route: str, user_text: str) -> str:
        """调用单个 LLM route"""
        if ":" not in route:
            raise ValueError(f"Invalid route format: {route}")

        provider, model = route.split(":", 1)
        prov_config = self.registry.get(provider) if self.registry else None

        if not prov_config:
            return self._call_direct(provider, model, user_text)

        return self._call_via_registry(provider, model, user_text, prov_config)

    def _call_via_registry(self, provider: str, model: str, user_text: str, prov_config: Any) -> str:
        """通过 registry 获取 provider 配置后调用"""
        adapter = self._get_adapter_for_provider(provider, prov_config)
        api_base = getattr(prov_config, "api_base_url", "")
        api_key = getattr(prov_config, "api_key", "")
        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        prov_dict = {
            "api_base_url": api_base,
            "protocol": getattr(prov_config, "protocol", ""),
            "providers_adapter": getattr(prov_config, "providers_adapter", ""),
        }
        target_url = adapter.get_target_url(prov_dict, model)

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
            resp = client.post(target_url, json=req_body, headers=headers)
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

    def _call_direct(self, provider: str, model: str, user_text: str) -> str:
        """直接调用（不依赖 registry），从 config 中查找 provider"""
        providers = self.config.get("providers", {})
        prov_data = providers.get(provider)
        if not prov_data:
            raise ValueError(f"Provider {provider} not found in config")

        api_base = prov_data.get("api_base_url", "")
        api_key = prov_data.get("api_key", "")
        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        adapter = self._get_adapter_for_provider(provider, prov_data)
        target_url = adapter.get_target_url(prov_data, model)

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
            resp = client.post(target_url, json=req_body, headers=headers)
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

    def _get_adapter_for_provider(self, provider_name: str, prov_config: Any):
        """获取 provider 对应的 adapter"""
        adapter_name = getattr(prov_config, "providers_adapter", "") or getattr(prov_config, "protocol", "")
        if adapter_name == "minimax":
            from ..protocol.minimax_adapter import MiniMaxAdapter
            return MiniMaxAdapter()
        elif adapter_name == "openai":
            from ..protocol.openai_adapter import OpenAIAdapter
            return OpenAIAdapter()
        else:
            from ..protocol.anthropic_adapter import AnthropicAdapter
            return AnthropicAdapter()

    def _resolve_route(self, intent: str, rules: list[dict]) -> tuple[str, list[str] | None]:
        kw_map = {"chat": "chat_intention", "task": "intention_analyze"}
        target_kw_group = kw_map.get(intent, "")
        for rule in rules:
            rule_kws = rule.get("keywords", [])
            wflow = self.keywords.get("workflow_intent", {})
            group_kws = wflow.get(target_kw_group, [])
            if any(kw in rule_kws for kw in group_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None
        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None

    def _keyword_fallback(self, body: dict) -> RoutingDecision:
        from .keyword import KeywordSplitter
        k = KeywordSplitter(config=self.config, keywords=self.keywords)
        return k.detect(body)

    def _extract_user_text(self, body: dict) -> str:
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