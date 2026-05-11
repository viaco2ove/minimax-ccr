---
title: CCRG 路由引擎设计
version: 0.1.0-draft
date: 2026-05-11
---

# 路由引擎设计

## 1. 核心流程

```
请求进入
    │
    ▼
┌─────────────────────────────────────────┐
│  Classifier — 提取请求特征               │
│                                         │
│  输入: raw_request                      │
│  输出: RequestTags                      │
│    .scenario: str | None               │
│    .tool_types: list[str]              │
│    .tool_details: list[ToolDetail]     │
│    .keywords: list[str]                │
│    .token_count: int                   │
│    .has_thinking: bool                 │
│    .has_images: bool                   │
│    .has_web_search: bool               │
│    .model_hint: str | None             │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  Router — 匹配路由规则                   │
│                                         │
│  按 routing.priority 顺序匹配：         │
│  1. scenario  → tags.scenario           │
│  2. tool_routing → tags.tool_types      │
│  3. keyword_routing → tags.keywords     │
│  4. default  → routing.default          │
│                                         │
│  输出: RouteResult                      │
│    .provider: str                       │
│    .model: str                          │
│    .fallback_chain: list[(str, str)]    │
│    .matched_rule: str                   │
│    .matched_reason: str                 │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  Capability Check — 能力校验             │
│                                         │
│  如果 route 的 provider 能力不满足：    │
│  - has_thinking && !cap.thinking        │
│  - has_images && !cap.vision            │
│  - has_web_search && !cap.tool_use      │
│  → 自动降级到 fallback 链               │
└─────────────┬───────────────────────────┘
              │
              ▼
         RouteResult (最终)
```

## 2. Classifier 详细设计

### 2.1 RequestTags 数据结构

```python
@dataclass
class ToolDetail:
    name: str           # tool 名称，如 "Read", "Bash"
    subcommand: str     # 子命令，如 "git status"（仅 Bash 等有）
    raw_input: dict     # tool 调用的原始 input

@dataclass
class RequestTags:
    scenario: str | None = None          # think / background / long_context / web_search / image
    tool_types: list[str] = field(default_factory=list)  # ["Read", "Bash", ...]
    tool_details: list[ToolDetail] = field(default_factory=list)  # 详细信息
    keywords: list[str] = field(default_factory=list)     # 命中的关键词
    token_count: int = 0                 # 估算的 token 数
    has_thinking: bool = False           # 请求包含 thinking 参数
    has_images: bool = False             # 请求包含图片
    has_web_search: bool = False         # 请求包含 web_search tools
    model_hint: str | None = None        # 从请求中提取的模型线索
```

### 2.2 ScenarioClassifier

从请求的结构化特征判断场景：

```python
class ScenarioClassifier:
    def classify(self, request: dict, config: dict) -> str | None:
        # 1. thinking 场景
        if request.get("thinking"):
            return "think"

        # 2. background 场景
        model = request.get("model", "")
        if "haiku" in model.lower() or self._is_background_hint(model, config):
            return "background"

        # 3. web_search 场景
        tools = request.get("tools", [])
        if any(t.get("type", "").startswith("web_search") for t in tools):
            return "web_search"

        # 4. image 场景
        if self._has_image_content(request):
            return "image"

        # 5. long_context 场景
        token_count = self._estimate_tokens(request)
        threshold = config.get("routing", {}).get("scenarios", {}).get("long_context", {}).get("threshold", 60000)
        if token_count > threshold:
            return "long_context"

        return None
```

### 2.3 ToolTypeClassifier

从消息中提取 tool 调用信息：

```python
class ToolTypeClassifier:
    def classify(self, request: dict) -> tuple[list[str], list[ToolDetail]]:
        tool_types = []
        tool_details = []

        messages = request.get("messages", [])
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        # Anthropic 格式: tool_result
                        if block.get("type") == "tool_result":
                            tool_id = block.get("tool_use_id", "")
                            name = self._resolve_tool_name(tool_id, messages)
                            if name and name not in tool_types:
                                tool_types.append(name)
                                tool_details.append(ToolDetail(
                                    name=name,
                                    subcommand=self._extract_subcommand(block),
                                    raw_input=block
                                ))
                        # Anthropic 格式: tool_use
                        elif block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name and name not in tool_types:
                                tool_types.append(name)
                                tool_details.append(ToolDetail(
                                    name=name,
                                    subcommand=self._extract_subcommand_from_input(block),
                                    raw_input=block.get("input", {})
                                ))

            elif isinstance(content, str):
                # OpenAI 格式的 tool 调用检测
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    if name and name not in tool_types:
                        tool_types.append(name)
                        tool_details.append(ToolDetail(
                            name=name,
                            subcommand="",
                            raw_input=json.loads(fn.get("arguments", "{}"))
                        ))

        return tool_types, tool_details

    def _extract_subcommand_from_input(self, tool_use_block: dict) -> str:
        """提取 Bash 等 tool 的子命令"""
        name = tool_use_block.get("name", "")
        inp = tool_use_block.get("input", {})
        if name == "Bash" and "command" in inp:
            return inp["command"]
        return ""
```

### 2.4 KeywordClassifier

从用户消息中提取关键词：

```python
class KeywordClassifier:
    def classify(self, request: dict, rules: list[dict]) -> list[str]:
        matched_keywords = []
        user_text = self._extract_user_text(request)

        for rule in rules:
            for keyword in rule.get("keywords", []):
                if keyword.lower() in user_text.lower():
                    matched_keywords.append(keyword)

        return matched_keywords
```

## 3. Router 详细设计

### 3.1 路由匹配流程

```python
class RoutingEngine:
    def __init__(self, config: dict, providers: dict):
        self.config = config
        self.providers = providers
        self.routing_config = config.get("routing", {})
        self.priority = self.routing_config.get("priority", ["scenario", "tool_routing", "keyword_routing", "default"])

    def route(self, tags: RequestTags) -> RouteResult:
        """按优先级依次匹配路由规则"""

        for strategy in self.priority:
            if strategy == "scenario" and tags.scenario:
                result = self._match_scenario(tags.scenario)
                if result:
                    return result

            elif strategy == "tool_routing" and tags.tool_types:
                result = self._match_tool_routing(tags.tool_types, tags.tool_details)
                if result:
                    return result

            elif strategy == "keyword_routing" and tags.keywords:
                result = self._match_keyword_routing(tags.keywords)
                if result:
                    return result

            elif strategy == "default":
                return self._match_default()

        return self._match_default()
```

### 3.2 场景路由匹配

```python
def _match_scenario(self, scenario: str) -> RouteResult | None:
    scenarios = self.routing_config.get("scenarios", {})
    rule = scenarios.get(scenario)
    if not rule:
        return None

    provider, model = self._parse_route(rule["route"])
    fallback_chain = [self._parse_route(f) for f in rule.get("fallback", [])]

    return RouteResult(
        provider=provider,
        model=model,
        fallback_chain=fallback_chain,
        matched_rule=f"scenario.{scenario}",
        matched_reason=f"scenario={scenario}"
    )
```

### 3.3 Tool 类型路由匹配

这是 CCRG 的核心创新。匹配逻辑：

```
对于每条 tool_routing 规则：
  1. 检查请求中的 tool_types 是否与 match 列表有交集
  2. 如果 match_mode=all：所有 tool_types 都必须在 match 列表中
  3. 如果 match_mode=any：任一 tool_type 在 match 列表中即可
  4. 模式匹配：ToolName(subcommand) 格式
     - Read → 精确匹配 tool name
     - Bash(git status) → 匹配 name=Bash 且 subcommand 前缀为 "git status"
     - Bash(git *) → 匹配 name=Bash 且 subcommand 前缀为 "git "
```

```python
def _match_tool_routing(self, tool_types: list[str], tool_details: list[ToolDetail]) -> RouteResult | None:
    tool_rules = self.routing_config.get("tool_routing", {})

    for rule_name, rule in tool_rules.items():
        match_patterns = rule.get("match", [])
        match_mode = rule.get("match_mode", "any")

        if match_mode == "any":
            # 任一 tool 命中即匹配
            for detail in tool_details:
                if self._match_tool_pattern(detail, match_patterns):
                    provider, model = self._parse_route(rule["route"])
                    fallback_chain = [self._parse_route(f) for f in rule.get("fallback", [])]
                    return RouteResult(
                        provider=provider, model=model,
                        fallback_chain=fallback_chain,
                        matched_rule=f"tool_routing.{rule_name}",
                        matched_reason=f"tool={detail.name}({detail.subcommand})"
                    )

        elif match_mode == "all":
            # 所有 tool 都必须在 match 列表中
            if all(self._match_tool_pattern(d, match_patterns) for d in tool_details):
                provider, model = self._parse_route(rule["route"])
                fallback_chain = [self._parse_route(f) for f in rule.get("fallback", [])]
                return RouteResult(
                    provider=provider, model=model,
                    fallback_chain=fallback_chain,
                    matched_rule=f"tool_routing.{rule_name}",
                    matched_reason=f"all_tools_matched"
                )

    return None


def _match_tool_pattern(self, detail: ToolDetail, patterns: list[str]) -> bool:
    """检查单个 tool 是否匹配 pattern 列表"""
    for pattern in patterns:
        if "(" in pattern:
            # 模式匹配: ToolName(subcommand)
            name_part, sub_part = pattern.split("(", 1)
            sub_part = sub_part.rstrip(")")

            if detail.name.lower() != name_part.lower():
                continue

            if sub_part.endswith("*"):
                # 通配符: 前缀匹配
                prefix = sub_part[:-1]
                if detail.subcommand.startswith(prefix):
                    return True
            else:
                # 精确子命令匹配
                if detail.subcommand == sub_part:
                    return True
        else:
            # 简单名称匹配
            if detail.name.lower() == pattern.lower():
                return True

    return False
```

### 3.4 关键词路由匹配

```python
def _match_keyword_routing(self, keywords: list[str]) -> RouteResult | None:
    keyword_rules = self.routing_config.get("keyword_routing", {}).get("rules", [])

    for rule in keyword_rules:
        rule_keywords = rule.get("keywords", [])
        match_mode = rule.get("match_mode", "any")

        if match_mode == "any":
            if any(kw in keywords for kw in rule_keywords):
                provider, model = self._parse_route(rule["route"])
                fallback_chain = [self._parse_route(f) for f in rule.get("fallback", [])]
                return RouteResult(
                    provider=provider, model=model,
                    fallback_chain=fallback_chain,
                    matched_rule=f"keyword_routing",
                    matched_reason=f"keyword={list(set(keywords) & set(rule_keywords))}"
                )

    return None
```

### 3.5 能力校验 + 自动降级

路由匹配完成后，检查目标 provider 的能力是否满足请求需求：

```python
def _check_capabilities(self, tags: RequestTags, result: RouteResult) -> RouteResult:
    """如果 provider 能力不满足，自动降级到 fallback"""
    provider_config = self.providers.get(result.provider, {})
    caps = provider_config.get("capabilities", {})

    reasons = []
    if tags.has_thinking and not caps.get("thinking", False):
        reasons.append("thinking not supported")
    if tags.has_images and not caps.get("vision", False):
        reasons.append("vision not supported")
    if tags.has_web_search and not caps.get("tool_use", False):
        reasons.append("tool_use not supported")

    if not reasons:
        return result

    # 能力不满足，尝试 fallback
    logger.warning(f"Provider {result.provider} lacks capabilities: {reasons}, trying fallback")
    for fb_provider, fb_model in result.fallback_chain:
        fb_caps = self.providers.get(fb_provider, {}).get("capabilities", {})
        if all([
            not (tags.has_thinking and not fb_caps.get("thinking", False)),
            not (tags.has_images and not fb_caps.get("vision", False)),
            not (tags.has_web_search and not fb_caps.get("tool_use", False)),
        ]):
            return RouteResult(
                provider=fb_provider, model=fb_model,
                fallback_chain=result.fallback_chain[1:],
                matched_rule=result.matched_rule,
                matched_reason=f"{result.matched_reason} → capability_fallback({reasons})"
            )

    # 所有 fallback 都不满足，还是用原 route（让请求去试，也许 provider 能处理）
    logger.error(f"No fallback provider satisfies capabilities: {reasons}")
    return result
```

## 4. Fallback 执行流程

```
主 Provider 请求
    │
    ├── 成功 → 返回
    │
    └── 失败（HTTP 4xx/5xx / 超时 / 连接失败）
        │
        ▼
    取 fallback_chain[0]
        │
        ├── 成功 → 返回
        │
        └── 失败 → 取 fallback_chain[1]
            │
            ├── ... 依次尝试
            │
            └── 全部失败 → 返回最后一个错误

```

关键行为：
1. **Fallback 不重跑路由引擎** — 直接使用 RouteResult 中的 fallback_chain
2. **Fallback 时需要重新做协议转换** — 因为 fallback provider 可能是不同的 protocol
3. **Fallback 请求是全新请求** — 不复用主 provider 的连接
4. **Fallback 不递归** — fallback provider 的请求不会再触发路由

## 5. Quota 管理

### 5.1 用量追踪

对于启用了 `track_usage` 的 quota，Gateway 记录每次请求的 token 消耗：

```python
class QuotaTracker:
    def __init__(self, config: dict):
        self.quotas = config.get("quota", {})
        self.usage = {}  # { quota_name: { "total_tokens": int, "request_count": int } }

    def record(self, provider: str, input_tokens: int, output_tokens: int):
        """记录一次请求的用量"""
        for name, q in self.quotas.items():
            if q.get("provider") == provider and q.get("track_usage"):
                if name not in self.usage:
                    self.usage[name] = {"total_tokens": 0, "request_count": 0}
                self.usage[name]["total_tokens"] += input_tokens + output_tokens
                self.usage[name]["request_count"] += 1

    def is_exhausted(self, provider: str) -> bool:
        """检查额度是否耗尽（需要 Provider API 支持）"""
        # 初期实现：简单标记，手动重置
        # 后续可以通过 Provider API 查询实际余额
        return False
```

### 5.2 额度耗尽处理

当 quota 检测到额度耗尽时：
1. 自动将后续请求路由到 `fallback_on_exhaust` 指定的 provider
2. 输出警告日志
3. 不影响其他 quota 的路由

## 6. 路由日志

每次路由决策都记录完整信息，便于事后分析和调优：

```json
{
  "timestamp": "2026-05-11T14:30:00.000Z",
  "request_id": "req_abc123",
  "tags": {
    "scenario": null,
    "tool_types": ["Read", "Grep"],
    "keywords": [],
    "token_count": 1234,
    "has_thinking": false,
    "has_images": false
  },
  "route": {
    "provider": "minimax",
    "model": "MiniMax-M2.7",
    "matched_rule": "tool_routing.cheap_tasks",
    "matched_reason": "tool=Read()"
  },
  "result": {
    "status": "success",
    "latency_ms": 1234,
    "input_tokens": 1000,
    "output_tokens": 500
  }
}
```

如果发生了 fallback，额外记录：

```json
{
  "fallback": {
    "from_provider": "minimax",
    "to_provider": "deepseek",
    "reason": "HTTP 429",
    "latency_ms": 2345
  }
}
```
