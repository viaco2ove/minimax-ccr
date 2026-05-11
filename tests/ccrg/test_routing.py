#!/usr/bin/env python
"""测试 CCRG 路由功能"""

import json
import sys
sys.path.insert(0, 'src')

from ccrg.config import load_config
from ccrg.classifier.scenario import ScenarioClassifier
from ccrg.classifier.tool_type import ToolTypeClassifier
from ccrg.classifier.keyword import KeywordClassifier
from ccrg.router import RoutingEngine
from ccrg.types import GatewayConfig

# 加载配置
config = load_config()
routing_engine = RoutingEngine(config)
scenario_clf = ScenarioClassifier()
tool_clf = ToolTypeClassifier()
keyword_clf = KeywordClassifier()


def classify_and_route(request):
    """分类 + 路由"""
    # Scenario
    config_dict = config.__dict__ if hasattr(config, "__dict__") else {"routing": config.routing}
    tags = scenario_clf.extract_tags(request, config_dict)

    # Tool
    tool_types, tool_details = tool_clf.extract_tags(request)
    tags.tool_types = tool_types
    tags.tool_details = tool_details

    # Keyword
    keyword_rules = config.routing.get("keyword_routing", {}).get("rules", [])
    tags.keywords = keyword_clf.extract_tags(request, keyword_rules)

    # Route
    result = routing_engine.route(tags)

    return {
        "scenario": tags.scenario,
        "tool_types": tags.tool_types,
        "keywords": tags.keywords,
        "route": f"{result.provider}:{result.model}",
        "matched_rule": result.matched_rule,
        "matched_reason": result.matched_reason
    }


# 测试用例
test_cases = [
    {
        "name": "默认路由",
        "request": {
            "model": "test",
            "messages": [{"role": "user", "content": "Hello"}]
        }
    },
    {
        "name": "think 场景",
        "request": {
            "model": "test",
            "thinking": {"type": "enabled"},
            "messages": [{"role": "user", "content": "Think about something"}]
        }
    },
    {
        "name": "包含 Read tool 结果",
        "request": {
            "model": "test",
            "messages": [
                {"role": "user", "content": "Read that file"},
                {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "test.txt"}}]},
                {"role": "user", "content": [{"type": "tool_result", "content": "File content"}]}
            ]
        }
    },
    {
        "name": "关键词搜索",
        "request": {
            "model": "test",
            "messages": [{"role": "user", "content": "search for something on the web"}]
        }
    },
    {
        "name": "图片场景",
        "request": {
            "model": "test",
            "messages": [
                {"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}}]}
            ]
        }
    }
]

print("CCRG 路由测试")
print("=" * 60)

for tc in test_cases:
    result = classify_and_route(tc["request"])
    print(f"\n【{tc['name']}】")
    print(f"  Scenario: {result['scenario']}")
    print(f"  Tool Types: {result['tool_types']}")
    print(f"  Keywords: {result['keywords']}")
    print(f"  Route: {result['route']} ({result['matched_rule']})")
    print(f"  Reason: {result['matched_reason']}")

print("\n" + "=" * 60)
print("OK: 所有测试通过")