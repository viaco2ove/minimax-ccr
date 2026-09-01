"""workflow 包内独立 LLM 策略（不复用下级 llm.py）。

配置直接从 config['workflow']['workflow_splitter']['llm_splitter'] 读取，
调用外部 LLM 分析命中关键词并返回 workflow 阶段。
"""

import json
import logging
import re
import time as _time
from typing import Any

import httpx

from ..base import RoutingDecision, resolve_workflow_stage
from .common import extract_user_text, get_workflow_splitter_config

logger = logging.getLogger("ccrg")


class WorkflowLLMStrategy:
    """使用外部 LLM 模型分析关键词并判定 workflow 阶段"""

    SYSTEM_PROMPT_TEMPLATE = """你是分流关键词匹配器。
输入: {user_content}

关键词库：
{keywords_text}

输出格式（仅JSON，禁止任何其他文字）：
{{"chat_intention":[],"intention_analyze":[],"problem_analyze":[],"solution_plan":[],"execute_solve":[]}}"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        self.config = config or {}
        self.keywords = keywords or {}
        self.registry = registry
        self.usage_stats = usage_stats

        llm_cfg = get_workflow_splitter_config(self.config).get("llm_splitter", {})
        if isinstance(llm_cfg, list):
            self.routes: list[str] = llm_cfg
        else:
            self.routes: list[str] = llm_cfg.get("routes", ["minimax:MiniMax-M2.7"]) if isinstance(llm_cfg, dict) else ["minimax:MiniMax-M2.7"]
        self.timeout = llm_cfg.get("timeout", 10.0) if isinstance(llm_cfg, dict) else 10.0

        # 动态构建关键词库文本
        wf = self.keywords.get("workflow_intent", {})
        lines = [f"- {cat}：{','.join(kws)}" for cat, kws in wf.items()]
        keywords_text = "\n".join(lines)
        self.system_prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            keywords_text=keywords_text,
            user_content="[用户输入将在此处替换]",
        )

        self.fallback: Any = None

        logger.info(f"[WorkflowLLMStrategy] configured: routes={self.routes}")

    def detect(self, body: dict) -> RoutingDecision:
        """使用 LLM 分析关键词并返回路由决策"""
        user_content = extract_user_text(body)
        if not user_content.strip():
            return self._keyword_fallback(body)

        for route in self.routes:
            start = _time.time()
            logger.info(f"[WorkflowLLMStrategy] >>> 尝试 route={route}, user_content={user_content[:50]}...")
            try:
                result = self._call_llm(route, user_content)
                elapsed = _time.time() - start
                if result:
                    matched = self._parse_llm_response(result)
                    route_str, fb, intent = self._resolve_route_from_keywords(matched)
                    category_scores = {
                        cat: max((s for _, s in items), default=0.0)
                        for cat, items in matched.items()
                    }
                    workflow_stage = resolve_workflow_stage(category_scores)
                    logger.info(
                        f"[WorkflowLLMStrategy] <<< 成功 route={route} | 耗时={elapsed:.1f}s | "
                        f"matched={matched} | intent={intent} | 最终route={route_str} | workflow_stage={workflow_stage}"
                    )
                    return RoutingDecision(
                        intent=intent,
                        route=route_str,
                        matched_rule="workflow_llm_routing",
                        matched_reason=f"keywords={matched}" if matched else "no_match",
                        fallback=fb,
                        workflow_stage=workflow_stage,
                    )
                logger.info(f"[WorkflowLLMStrategy] <<< route={route} 返回空结果，继续下一个 | elapsed={elapsed:.1f}s")
            except Exception as e:
                logger.warning(f"[WorkflowLLMStrategy] <<< route={route} 失败: {e} | 耗时={_time.time() - start:.1f}s")
                continue

        logger.info("[WorkflowLLMStrategy] 所有 route 均失败，回退到关键词策略")
        return self._keyword_fallback(body)

    def _parse_llm_response(self, text: str) -> dict:
        """解析 LLM 返回的 JSON，返回 categories -> [(kw, score)]"""
        text = text.strip()
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            try:
                arr = json.loads(text)
                if isinstance(arr, list):
                    return {"chat_intention": [(kw, 1.0) if isinstance(kw, str) else kw for kw in arr]}
            except Exception:
                pass
            return {}

        try:
            data = json.loads(json_match.group())
            result = {}
            for cat, val in data.items():
                if not isinstance(val, list):
                    continue
                items = []
                for item in val:
                    if isinstance(item, str):
                        items.append((item, 1.0))
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        items.append((str(item[0]), float(item[1])))
                if items:
                    result[cat] = items
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"[WorkflowLLMStrategy] JSON 解析失败: {e}")
            return {}

    def _resolve_route_from_keywords(self, matched: dict) -> tuple[str, list[str] | None, str]:
        """根据命中关键词从 keyword_routing.rules 找路由"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        def extract_kws(cat_matched):
            kws = []
            for item in cat_matched:
                if isinstance(item, (list, tuple)):
                    kws.append(item[0])
                else:
                    kws.append(item)
            return kws

        chat_kws = extract_kws(matched.get("chat_intention", []))
        task_kws = extract_kws(matched.get("intention_analyze", []))

        if len(task_kws) > len(chat_kws):
            intent = "task"
            matched_kws = task_kws
        else:
            intent = "chat"
            matched_kws = chat_kws if chat_kws else task_kws

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
        prompt = self.system_prompt.replace("[用户输入将在此处替换]", user_content)
        return [{"role": "system", "content": prompt}]

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

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        self._record_usage(provider, model, data)

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

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        self._record_usage(provider, model, data)

        content = data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def _record_usage(self, provider: str, model: str, resp_data: dict):
        if not self.usage_stats:
            return
        usage = resp_data.get("usage", {})
        self.usage_stats.record(
            provider=provider,
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=0,
            success=True,
            route_rule="workflow_llm_splitter",
        )

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
        from .keyword import WorkflowKeywordStrategy
        return WorkflowKeywordStrategy(config=self.config, keywords=self.keywords).detect(body)
