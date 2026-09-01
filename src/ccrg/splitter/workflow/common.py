"""workflow 包内共享工具：文本提取、关键词匹配、路由解析。

本模块为 workflow 独立分流器专用，不复用下级 splitter 的实现代码。
"""

import re


def get_workflow_splitter_config(config) -> dict:
    """从 config 提取 workflow.workflow_splitter 配置 dict。

    兼容两种传入：
    - config 为 dict（含 "workflow" 键，其值为 WorkflowConfig 对象或 dict）
    - config 本身为 GatewayConfig 对象（含 .workflow 属性）
    """
    if config is None:
        return {}
    wf_sec = config.get("workflow", {}) if isinstance(config, dict) else getattr(config, "workflow", None)
    if wf_sec is None:
        return {}
    if isinstance(wf_sec, dict):
        return wf_sec.get("workflow_splitter", {}) or {}
    getter = getattr(wf_sec, "get_workflow_splitter_config", None)
    if callable(getter):
        return getter() or {}
    return {}


def extract_user_text(body: dict) -> str:
    """提取用户消息文本，剥离 <system-reminder> 块以减少会话压缩摘要的干扰"""
    texts = []

    for msg in body.get("messages", []):
        role = msg.get("role", "")
        if role != "user":
            continue

        content = msg.get("content", "")
        if isinstance(content, str):
            joined = content
        elif isinstance(content, list):
            joined = " ".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            joined = ""

        # 剥离 system-reminder 块，减少压缩摘要的噪声干扰
        joined = re.sub(r"<system-reminder[^>]*>.*?</system-reminder>", " ", joined, flags=re.DOTALL)
        texts.append(joined)

    return " ".join(texts).strip()


def word_match(kw: str, text: str) -> bool:
    """单词边界匹配，避免子串误匹配（如 'hi' 命中 'think'）"""
    pattern = r'\b' + re.escape(kw.lower()) + r'\b'
    return bool(re.search(pattern, text))


def resolve_route_from_keywords(config: dict, keywords: dict, intent: str) -> tuple[str, list[str] | None]:
    """从 routing.keyword_routing.rules 中找 intent 对应的路由（对齐下级 KeywordSplitter._resolve_route）。

    intent: "chat" | "task"
    """
    # keywords.json 中 chat 对应 chat_intention，task 对应 intention_analyze
    kw_map = {"chat": "chat_intention", "task": "intention_analyze"}
    target_kw_group = kw_map.get(intent, "")

    rules = config.get("routing", {}).get("keyword_routing", {}).get("rules", [])
    wflow = keywords.get("workflow_intent", {})
    group_kws = wflow.get(target_kw_group, [])

    for rule in rules:
        rule_kws = rule.get("keywords", [])
        if any(kw in rule_kws for kw in group_kws):
            route = rule.get("route", "")
            fb = rule.get("fallback", [])
            return route, fb if fb else None

    default = config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
    return default, None
