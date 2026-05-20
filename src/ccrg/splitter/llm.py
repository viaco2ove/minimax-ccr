"""
LLMSplitter — 基于 LLM 模型的工作流意图分流器。

通过配置的 provider:model 调用外部 LLM，分析命中关键词并返回路由。
取代 keyword_routing。
"""

import json
import logging
import re
from typing import Any

import httpx

from .base import RoutingDecision, Splitter

logger = logging.getLogger("ccrg")


class LLMSplitter(Splitter):
    """使用 LLM 模型分析关键词并返回路由 — 取代 keyword_routing"""

    SYSTEM_PROMPT = """你是请求分流关键词匹配器，**仅输出纯JSON，禁止任何多余文字、解释、备注**。按给定关键词库精准匹配，命中就填入对应数组，无匹配字段直接省略。输出格式严格遵循示例：{"workflow_intent":{"chat_intention":["咋样"]}}
关键词库：{keywords_json}"""

    USER_PROMPT_TEMPLATE = """{user_content}

<instruction>
作为模型分流器，请根据上文提供的关键词列表，分析用户的 user_query 命中了哪些关键词。
必须严格且仅输出 JSON 格式数据，不要包含任何思考过程、不要使用 Markdown 代码块（如 ```json）、不要有任何其他自然语言废话。
示例格式：
{{
  "workflow_intent": {{
    "intention_analyze": ["帮我"]
  }},
  "task_routing": {{
    "cheap_tasks": ["查看"]
  }}
}}
</instruction>"""

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
        self.timeout = llm_cfg.get("timeout", 10.0) if isinstance(llm_cfg, dict) else 10.0

        # 组装 system prompt，替换 {keywords_json}
        keywords_str = json.dumps(keywords, ensure_ascii=False)
        self.system_prompt = self.SYSTEM_PROMPT.replace("{keywords_json}", keywords_str)

        self.fallback_splitter: Splitter | None = None

        logger.info(f"[LLMSplitter] configured: routes={self.routes}")

    def detect(self, body: dict) -> RoutingDecision:
        """使用 LLM 分析关键词并返回路由决策"""
        user_content = self._extract_user_text(body)
        if not user_content.strip():
            return self._keyword_fallback(body)

        for route in self.routes:
            try:
                logger.debug(f"[LLMSplitter]_call_llm start")
                result = self._call_llm(route, user_content)
                logger.debug(f"[LLMSplitter]_call_llm end, result length={len(result) if result else 0}")
                if result:
                    logger.debug(f"[LLMSplitter] result preview: {result[:200] if len(result) > 200 else result}")
                    matched = self._parse_llm_response(result)
                    logger.debug(f"[LLMSplitter] parsed matched: {matched}")
                    if matched:
                        route_str, fb, intent = self._resolve_route_from_keywords(matched)
                        return RoutingDecision(
                            intent=intent,
                            route=route_str,
                            matched_rule="llm_routing",
                            matched_reason=f"keywords={matched}",
                            fallback=fb,
                        )
                logger.debug(f"[LLMSplitter] {route} returned invalid response")
            except Exception as e:
                import traceback
                logger.debug(f"[LLMSplitter] {route} failed: {e}\n{traceback.format_exc()}")
                continue

        logger.debug(f"[LLMSplitter] all routes failed, using keyword fallback")
        return self._keyword_fallback(body)

    def _parse_llm_response(self, text: str) -> dict:
        """解析 LLM 返回的 JSON，返回 workflow_intent（可能为空 dict）"""
        text = text.strip()
        logger.debug(f"[LLMSplitter] _parse_llm_response input: {text[:300] if len(text) > 300 else text}")
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.debug(f"[LLMSplitter] no JSON found in response")
            return {}
        try:
            data = json.loads(json_match.group())
            logger.debug(f"[LLMSplitter] parsed JSON keys: {list(data.keys())}")
            result = data.get("workflow_intent", {})
            logger.debug(f"[LLMSplitter] workflow_intent: {result}")
            return result if result else {}
        except json.JSONDecodeError as e:
            logger.debug(f"[LLMSplitter] JSON parse error: {e}")
            return {}

    def _resolve_route_from_keywords(self, matched: dict) -> tuple[str, list[str] | None, str]:
        """根据命中的关键词从 keyword_routing.rules 找路由"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        chat_matched = matched.get("chat_intention", [])
        task_matched = matched.get("intention_analyze", [])

        if len(task_matched) > len(chat_matched):
            intent = "task"
            matched_kws = task_matched
        else:
            intent = "chat"
            matched_kws = chat_matched if chat_matched else task_matched

        for rule in rules:
            rule_kws = rule.get("keywords", [])
            if any(kw in rule_kws for kw in matched_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None, intent

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None, intent

    def _call_llm(self, route: str, user_content: str) -> str:
        """调用单个 LLM route，返回原始文本"""
        if ":" not in route:
            raise ValueError(f"Invalid route format: {route}")

        provider, model = route.split(":", 1)
        prov_config = self.registry.get(provider) if self.registry else None

        if not prov_config:
            return self._call_direct(provider, model, user_content)

        return self._call_via_registry(provider, model, user_content, prov_config)

    def _build_messages(self, user_content: str) -> list:
        """构建 messages 数组，符合 Anthropic 格式"""
        return [
            {
                "role": "system",
                "content": [{"type": "text", "text": self.system_prompt}]
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": self.USER_PROMPT_TEMPLATE.format(user_content=user_content)}]
            }
        ]

    def _call_via_registry(self, provider: str, model: str, user_content: str, prov_config: Any) -> str:
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

        logger.debug(f"[LLMSplitter]_build_messages start")
        messages = self._build_messages(user_content)
        logger.debug(f"[LLMSplitter]_build_messages end")
        req_body = {
            "model": model,
            "messages": messages,
            "max_tokens": 500,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        # 输出 curl 命令
        import shlex
        curl_cmd = f"curl -X POST {shlex.quote(target_url)} "
        for k, v in headers.items():
            curl_cmd += f"-H {shlex.quote(f'{k}: {v}')} "
        curl_cmd += f"-d {shlex.quote(json.dumps(req_body, ensure_ascii=False))}"
        logger.debug(f"[LLMSplitter][curl]\n{curl_cmd}")

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def _call_direct(self, provider: str, model: str, user_content: str) -> str:
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

        messages = self._build_messages(user_content)

        req_body = {
            "model": model,
            "messages": messages,
            "max_tokens": 500,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        # 输出 curl 命令
        import shlex
        curl_cmd = f"curl -X POST {shlex.quote(target_url)} "
        for k, v in headers.items():
            curl_cmd += f"-H {shlex.quote(f'{k}: {v}')} "
        curl_cmd += f"-d {shlex.quote(json.dumps(req_body, ensure_ascii=False))}"
        logger.debug(f"[LLMSplitter][curl]\n{curl_cmd}")

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def _get_adapter_for_provider(self, provider_name: str, prov_config: Any):
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

    def _keyword_fallback(self, body: dict) -> RoutingDecision:
        from .keyword import KeywordSplitter
        k = KeywordSplitter(config=self.config, keywords=self.keywords)
        return k.detect(body)

    def _extract_user_text(self, body: dict) -> str:
        texts = []
        for msg in body.get("messages", []):
            if msg.get("role") != "user":
                continue
            c = msg.get("content", "")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
        return " ".join(texts)