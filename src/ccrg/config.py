"""
配置加载和校验模块。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from .types import GatewayConfig

logger = logging.getLogger("ccrg.config")


def resolve_env_var(value: str) -> str:
    """解析环境变量引用，支持 $VAR 和 ${VAR} 格式"""
    if not isinstance(value, str):
        return value

    # 匹配 $VAR 或 ${VAR}
    pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'

    def replacer(match):
        var_name = match.group(1) or match.group(2)
        env_value = os.environ.get(var_name, "")
        if not env_value and os.environ.get(f"{var_name}_FILE"):
            # 支持从文件读取
            file_path = os.environ[f"{var_name}_FILE"]
            try:
                env_value = Path(file_path).read_text().strip()
            except Exception:
                pass
        return env_value

    return re.sub(pattern, replacer, value)


def resolve_config_env_vars(config: Any) -> Any:
    """递归解析配置中的所有环境变量"""
    if isinstance(config, dict):
        return {k: resolve_config_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [resolve_config_env_vars(item) for item in config]
    elif isinstance(config, str):
        return resolve_env_var(config)
    return config


def load_config(config_path: str | Path | None = None) -> GatewayConfig:
    """加载并校验配置文件"""
    if config_path is None:
        # 默认在项目根目录
        config_path = Path(__file__).parent.parent.parent / ".gateway.json"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    logger.info(f"Loading config from {config_path}")

    # 加载 JSON（支持注释）
    with open(config_path, encoding="utf-8") as f:
        raw_config = json.loads(_strip_comments(f.read()))

    # 解析环境变量
    config = resolve_config_env_vars(raw_config)

    # 转换为强类型配置
    gateway_config = GatewayConfig.from_dict(config)

    # 校验配置
    validate_config(gateway_config)

    logger.info(f"Loaded {len(gateway_config.providers)} providers, routing priority: {gateway_config.routing.get('priority', [])}")

    # 加载 keywords.json
    keywords_path = config_path.parent / "keywords.json"
    if keywords_path.exists():
        with open(keywords_path, encoding="utf-8") as f:
            gateway_config.keywords = json.load(f)
        logger.info(f"Loaded keywords from {keywords_path}")
    else:
        logger.warning(f"keywords.json not found at {keywords_path}, workflow intent detection will use default logic")

    return gateway_config


def _strip_comments(text: str) -> str:
    """移除 JSON 中的注释（智能处理 URL 中的 //）"""
    lines = []
    for line in text.splitlines():
        # 只移除不在字符串内的 // 注释
        stripped = _remove_comment(line)
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def _remove_comment(line: str) -> str:
    """移除行内注释，只在不在字符串内时处理 //"""
    result = []
    i = 0
    in_string = False
    escape_next = False

    while i < len(line):
        char = line[i]

        if escape_next:
            result.append(char)
            escape_next = False
            i += 1
            continue

        if char == '\\' and in_string:
            result.append(char)
            escape_next = True
            i += 1
            continue

        if char == '"' and not in_string:
            in_string = True
            result.append(char)
            i += 1
            continue

        if char == '"' and in_string:
            in_string = False
            result.append(char)
            i += 1
            continue

        if char == '/' and i + 1 < len(line) and line[i + 1] == '/' and not in_string:
            # 这是一个注释，停止处理
            break

        result.append(char)
        i += 1

    return ''.join(result).rstrip()


def validate_config(config: GatewayConfig) -> None:
    """校验配置合法性"""
    errors = []

    # 1. routing.default 必须存在
    if "default" not in config.routing:
        errors.append("routing.default is required")

    # 2. 至少有一个 provider
    if not config.providers:
        errors.append("At least one provider is required")

    # 3. 所有 route 和 fallback 中的 provider:model 必须存在
    all_routes = _collect_all_routes(config)
    for route_str in all_routes:
        if ":" not in route_str:
            errors.append(f"Invalid route format: {route_str} (expected 'provider:model')")
            continue
        provider, model = route_str.split(":", 1)
        if provider not in config.providers:
            errors.append(f"Unknown provider in route: {route_str}")
        elif model and model not in config.providers[provider].models:
            errors.append(f"Unknown model '{model}' in provider '{provider}'")

    # 4. scenario 路由的 provider 能力校验
    for scenario, rule in config.routing.get("scenarios", {}).items():
        route = rule.get("route", "")
        if ":" not in route:
            continue
        provider = route.split(":")[0]
        caps = config.providers.get(provider, GatewayConfig._NOT_FOUND).capabilities if hasattr(GatewayConfig, '_NOT_FOUND') else {}

        # 简化校验：后续再做详细能力检查
        _ = provider, caps  # 暂时跳过

    # 5. routing.priority 必须是合法的策略名
    priority = config.routing.get("priority", [])
    valid_priorities = {"scenario", "tool_routing", "keyword_routing", "default"}
    for p in priority:
        if p not in valid_priorities:
            errors.append(f"Invalid routing priority: {p}")

    # 6. 环境变量检查（api_key 可以为空，只要不是 $VAR 形式未解析）
    for name, provider in config.providers.items():
        # 如果 api_key 是 $VAR 形式但未解析，警告但不阻止启动
        pass

    if errors:
        raise ValueError(f"Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def _collect_all_routes(config: GatewayConfig) -> set[str]:
    """收集配置中所有用到的 route 字符串"""
    routes = set()

    # routing.default
    if "default" in config.routing:
        routes.add(config.routing["default"])

    # routing.scenarios
    for scenario, rule in config.routing.get("scenarios", {}).items():
        if "route" in rule:
            routes.add(rule["route"])
        for fb in rule.get("fallback", []):
            routes.add(fb)

    # routing.tool_routing
    for rule in config.routing.get("tool_routing", {}).values():
        if "route" in rule:
            routes.add(rule["route"])
        for fb in rule.get("fallback", []):
            routes.add(fb)

    # routing.keyword_routing
    for rule in config.routing.get("keyword_routing", {}).get("rules", []):
        if "route" in rule:
            routes.add(rule["route"])
        for fb in rule.get("fallback", []):
            routes.add(fb)

    return routes
