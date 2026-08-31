"""
CCRG core data types.
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ToolDetail:
    """单个 tool 调用的详细信息"""
    name: str           # tool 名称，如 "Read", "Bash"
    subcommand: str     # 子命令，如 "git status"（仅 Bash 等有）
    raw_input: dict     # tool 调用的原始 input

    def match_pattern(self, pattern: str) -> bool:
        """检查是否匹配 pattern"""
        if "(" in pattern:
            # 模式匹配: ToolName(subcommand)
            name_part, sub_part = pattern.split("(", 1)
            sub_part = sub_part.rstrip(")")

            if self.name.lower() != name_part.lower():
                return False

            if sub_part.endswith("*"):
                # 通配符: 前缀匹配
                prefix = sub_part[:-1]
                return self.subcommand.startswith(prefix)
            else:
                # 精确子命令匹配
                return self.subcommand == sub_part
        else:
            # 简单名称匹配
            return self.name.lower() == pattern.lower()


@dataclass
class RequestTags:
    """请求特征标签"""
    scenario: str | None = None          # think / background / long_context / web_search / image
    tool_types: list[str] = field(default_factory=list)  # ["Read", "Bash", ...]
    tool_details: list[ToolDetail] = field(default_factory=list)  # 详细信息
    keywords: list[str] = field(default_factory=list)     # 命中的关键词
    token_count: int = 0                 # 估算的 token 数
    has_thinking: bool = False           # 请求包含 thinking 参数
    has_images: bool = False             # 请求包含图片
    has_web_search: bool = False        # 请求包含 web_search tools
    model_hint: str | None = None        # 从请求中提取的模型线索


@dataclass
class RouteResult:
    """路由结果"""
    provider: str                          # provider 名称
    model: str                             # 模型名称
    fallback_chain: list[tuple[str, str]] = field(default_factory=list)  # [(provider, model), ...]
    matched_rule: str = ""                # 匹配的规则名，如 "tool_routing.cheap_tasks"
    matched_reason: str = ""              # 匹配原因，如 "tool=Read()"


@dataclass
class ProviderConfig:
    """Provider 配置"""
    name: str
    api_base_url: str
    api_key: str
    protocol: Literal["codeplan_anthropic", "chat_openai", "mmx"]
    models: list[str]
    capabilities: dict = field(default_factory=dict)
    cost_tier: str = "standard"
    default_params: dict = field(default_factory=dict)
    retry: dict = field(default_factory=dict)
    timeout_ms: int | None = None
    per_request_delay_ms: int | None = None
    providers_adapter: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "ProviderConfig":
        return cls(
            name=name,
            api_base_url=data.get("api_base_url", ""),
            api_key=data.get("api_key", ""),
            protocol=data.get("protocol", "anthropic"),
            models=data.get("models", []),
            capabilities=data.get("capabilities", {}),
            cost_tier=data.get("cost_tier", "standard"),
            default_params=data.get("default_params", {}),
            retry=data.get("retry", {}),
            timeout_ms=data.get("timeout_ms"),
            per_request_delay_ms=data.get("per_request_delay_ms"),
            providers_adapter=data.get("providers_adapter", ""),
        )


@dataclass
class WorkflowConfig:
    """Workflow 配置"""
    enabled: bool = False
    intention_analyze: list[str] | str = "minimax:MiniMax-M2.7"
    chat_intention: list[str] | str = "minimax:MiniMax-M2.7"
    analyze_plan: list[str] | str = "qianfan:qianfan-code-latest"
    execute_solve: list[str] | str = "minimax:MiniMax-M2.7"
    workflow_splitter: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowConfig":
        return cls(
            enabled=data.get("enabled", False),
            intention_analyze=data.get("intention_analyze", "minimax:MiniMax-M2.7"),
            chat_intention=data.get("chat_intention", "minimax:MiniMax-M2.7"),
            analyze_plan=data.get("analyze_plan", data.get("problem_analyze", "qianfan:qianfan-code-latest")),
            execute_solve=data.get("execute_solve", "minimax:MiniMax-M2.7"),
            workflow_splitter=data.get("workflow_splitter", {}),
        )

    def get_workflow_splitter_config(self) -> dict:
        """获取 workflow 独立 splitter 配置段"""
        return self.workflow_splitter if isinstance(self.workflow_splitter, dict) else {}

    def is_workflow_splitter_enabled(self) -> bool:
        """workflow 独立 splitter 是否启用"""
        return bool(self.get_workflow_splitter_config().get("enabled", False))

    def get_intention_analyze_list(self) -> list[str]:
        """获取 intention_analyze 列表"""
        if isinstance(self.intention_analyze, str):
            return [self.intention_analyze]
        return self.intention_analyze

    def get_chat_intention_list(self) -> list[str]:
        """获取 chat_intention 列表"""
        if isinstance(self.chat_intention, str):
            return [self.chat_intention]
        return self.chat_intention

    def get_analyze_plan_list(self) -> list[str]:
        """获取 analyze_plan 列表"""
        if isinstance(self.analyze_plan, str):
            return [self.analyze_plan]
        return self.analyze_plan

    def get_execute_solve_list(self) -> list[str]:
        """获取 execute_solve 列表"""
        if isinstance(self.execute_solve, str):
            return [self.execute_solve]
        return self.execute_solve

    def get_chat_intention_list(self) -> list[str]:
        """获取 chat_intention 列表"""
        if isinstance(self.chat_intention, str):
            return [self.chat_intention]
        return self.chat_intention

    def get_intention_analyze_list(self) -> list[str]:
        """获取 intention_analyze 列表"""
        if isinstance(self.intention_analyze, str):
            return [self.intention_analyze]
        return self.intention_analyze

    def get_chat_intention_single(self) -> str:
        """获取单个 chat_intention（取第一个）"""
        if isinstance(self.chat_intention, str):
            return self.chat_intention
        return self.chat_intention[0] if self.chat_intention else "minimax:MiniMax-M2.7"

    def get_execute_solve_single(self) -> str:
        """获取单个 execute_solve（取第一个）"""
        if isinstance(self.execute_solve, str):
            return self.execute_solve
        return self.execute_solve[0] if self.execute_solve else "minimax:MiniMax-M2.7"


@dataclass
class GatewayConfig:
    """Gateway 完整配置"""
    server: dict
    providers: dict[str, ProviderConfig]
    routing: dict
    quota: dict
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    keywords: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "GatewayConfig":
        providers = {}
        for name, pdata in data.get("providers", {}).items():
            providers[name] = ProviderConfig.from_dict(name, pdata)

        return cls(
            server=data.get("server", {}),
            providers=providers,
            routing=data.get("routing", {}),
            quota=data.get("quota", {}),
            workflow=WorkflowConfig.from_dict(data.get("workflow", {})),
            keywords={},
        )
