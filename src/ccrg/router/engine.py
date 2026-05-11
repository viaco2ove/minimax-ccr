"""
路由引擎 — 核心路由决策逻辑。
"""

import logging
from typing import Any

from ..types import GatewayConfig, ProviderConfig, RequestTags, RouteResult

logger = logging.getLogger("ccrg.router")


class RoutingEngine:
    """路由引擎

    根据请求特征和配置的路由规则，决定请求发往哪个 Provider。
    """

    def __init__(self, config: GatewayConfig):
        self._config = config
        self._routing_config = config.routing
        self._providers = config.providers
        self._priority = self._routing_config.get("priority", [
            "scenario", "tool_routing", "keyword_routing", "default"
        ])

    def route(self, tags: RequestTags) -> RouteResult:
        """执行路由决策

        按 routing.priority 顺序匹配路由策略，先命中先执行。
        """
        for strategy in self._priority:
            result = None

            if strategy == "scenario":
                result = self._match_scenario(tags)
            elif strategy == "tool_routing":
                result = self._match_tool_routing(tags)
            elif strategy == "keyword_routing":
                result = self._match_keyword_routing(tags)
            elif strategy == "default":
                result = self._match_default()
                # default 是兜底，直接返回
                return self._check_capabilities(tags, result)

            if result:
                # 检查 provider 能力
                result = self._check_capabilities(tags, result)
                logger.debug(f"Routed to {result.provider}:{result.model} via {result.matched_rule}")
                return result

        # 兜底到 default
        return self._check_capabilities(tags, self._match_default())

    def _match_scenario(self, tags: RequestTags) -> RouteResult | None:
        """匹配场景路由"""
        if not tags.scenario:
            return None

        scenarios = self._routing_config.get("scenarios", {})
        rule = scenarios.get(tags.scenario)
        if not rule:
            return None

        return self._build_route_result(rule, f"scenario.{tags.scenario}", f"scenario={tags.scenario}")

    def _match_tool_routing(self, tags: RequestTags) -> RouteResult | None:
        """匹配 tool 类型路由（核心创新）"""
        if not tags.tool_types and not tags.tool_details:
            return None

        tool_rules = self._routing_config.get("tool_routing", {})

        for rule_name, rule in tool_rules.items():
            match_patterns = rule.get("match", [])
            match_mode = rule.get("match_mode", "any")

            if not match_patterns:
                continue

            if match_mode == "any":
                # 任一 tool 命中即匹配
                for detail in tags.tool_details:
                    if self._match_tool_pattern(detail, match_patterns):
                        return self._build_route_result(
                            rule,
                            f"tool_routing.{rule_name}",
                            f"tool={detail.name}({detail.subcommand})"
                        )

            elif match_mode == "all":
                # 所有 tool 都必须在 match 列表中
                if all(self._match_tool_pattern(d, match_patterns) for d in tags.tool_details):
                    return self._build_route_result(
                        rule,
                        f"tool_routing.{rule_name}",
                        "all_tools_matched"
                    )

        return None

    def _match_tool_pattern(self, detail: Any, patterns: list[str]) -> bool:
        """检查单个 tool 是否匹配 pattern 列表"""
        for pattern in patterns:
            if detail.match_pattern(pattern):
                return True
        return False

    def _match_keyword_routing(self, tags: RequestTags) -> RouteResult | None:
        """匹配关键词路由"""
        if not tags.keywords:
            return None

        keyword_rules = self._routing_config.get("keyword_routing", {}).get("rules", [])

        for rule in keyword_rules:
            rule_keywords = rule.get("keywords", [])
            match_mode = rule.get("match_mode", "any")

            if match_mode == "any":
                if any(kw in tags.keywords for kw in rule_keywords):
                    return self._build_route_result(
                        rule,
                        "keyword_routing",
                        f"keyword={list(set(tags.keywords) & set(rule_keywords))}"
                    )

        return None

    def _match_default(self) -> RouteResult:
        """默认路由"""
        default_route = self._routing_config.get("default", "minimax:MiniMax-M2.7")
        return RouteResult(
            provider=default_route.split(":")[0],
            model=default_route.split(":")[1] if ":" in default_route else "",
            matched_rule="default",
            matched_reason="fallback"
        )

    def _build_route_result(self, rule: dict, matched_rule: str, matched_reason: str) -> RouteResult:
        """从规则构建 RouteResult"""
        route_str = rule.get("route", "")
        if ":" not in route_str:
            logger.warning(f"Invalid route format: {route_str}, falling back to default")
            return self._match_default()

        provider = route_str.split(":")[0]
        model = route_str.split(":", 1)[1]

        # 构建 fallback 链
        fallback_chain = []
        for fb in rule.get("fallback", []):
            if ":" in fb:
                fb_provider = fb.split(":")[0]
                fb_model = fb.split(":", 1)[1]
                fallback_chain.append((fb_provider, fb_model))

        return RouteResult(
            provider=provider,
            model=model,
            fallback_chain=fallback_chain,
            matched_rule=matched_rule,
            matched_reason=matched_reason
        )

    def _check_capabilities(self, tags: RequestTags, result: RouteResult) -> RouteResult:
        """检查 provider 能力是否满足请求需求，不满足则降级"""
        provider_config = self._providers.get(result.provider)
        if not provider_config:
            return result

        caps = provider_config.capabilities
        reasons = []

        if tags.has_thinking and not caps.get("thinking", False):
            reasons.append("thinking not supported")
        if tags.has_images and not caps.get("vision", False):
            reasons.append("vision not supported")
        if tags.has_web_search and not caps.get("tool_use", False):
            reasons.append("tool_use not supported")

        if not reasons:
            return result

        # 尝试 fallback
        logger.warning(f"Provider {result.provider} lacks capabilities: {reasons}, trying fallback")
        for fb_provider, fb_model in result.fallback_chain:
            fb_config = self._providers.get(fb_provider)
            if not fb_config:
                continue

            fb_caps = fb_config.capabilities

            # 检查 fallback 是否满足能力需求
            can_handle = True
            if tags.has_thinking and not fb_caps.get("thinking", False):
                can_handle = False
            if tags.has_images and not fb_caps.get("vision", False):
                can_handle = False
            if tags.has_web_search and not fb_caps.get("tool_use", False):
                can_handle = False

            if can_handle:
                # 构建新的 fallback 链（去掉已经尝试过的和不能满足能力的）
                new_chain = []
                for fb_p, fb_m in [(result.provider, result.model)] + result.fallback_chain:
                    # 跳过不能满足当前请求能力的 provider
                    fb_c = self._providers.get(fb_p)
                    if not fb_c:
                        continue
                    fb_c_caps = fb_c.capabilities
                    skip = False
                    if tags.has_thinking and not fb_c_caps.get("thinking", False):
                        skip = True
                    if tags.has_images and not fb_c_caps.get("vision", False):
                        skip = True
                    if tags.has_web_search and not fb_c_caps.get("tool_use", False):
                        skip = True
                    if not skip:
                        new_chain.append((fb_p, fb_m))

                return RouteResult(
                    provider=fb_provider,
                    model=fb_model,
                    fallback_chain=new_chain,
                    matched_rule=result.matched_rule,
                    matched_reason=f"{result.matched_reason} → capability_fallback({reasons})"
                )

        # 所有 fallback 都不满足，还是用原 route
        logger.error(f"No fallback provider satisfies capabilities: {reasons}")
        return result
