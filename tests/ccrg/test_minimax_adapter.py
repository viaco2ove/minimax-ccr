#!/usr/bin/env python
"""测试 MiniMaxAdapter 的思考开关 / effort 注入逻辑

覆盖：
  - reasoning_effort="none"        → 注入 thinking={"type":"disabled"} 关闭思考
  - capabilities.thinking=False    → 注入 thinking={"type":"disabled"} 关闭思考
  - reasoning_effort="low" 等合法值 → 注入 output_config={"effort":"low"} 控制深度
  - 两者都未配置                    → 不注入任何思考/effort 参数
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ccrg.protocol.minimax_adapter import MiniMaxAdapter


def base_request():
    return {
        "model": "MiniMax-M3.1-Flash",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
        "system": "sys",
        "max_tokens": 22000,
    }


def make_prov(**caps_extra):
    caps = {"thinking": True, "reasoning_effort": None}
    caps.update(caps_extra)
    return {
        "api_base_url": "https://api.minimaxi.com/anthropic",
        "protocol": "codeplan_anthropic",
        "providers_adapter": "minimax",
        "models": ["MiniMax-M3.1-Flash"],
        "capabilities": caps,
        "default_params": {"max_tokens": 40096},
    }


class TestMiniMaxAdapterThinking(unittest.TestCase):
    def test_effort_none_disables_thinking(self):
        prov = make_prov(reasoning_effort="none", thinking=True)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertEqual(out.get("thinking"), {"type": "disabled"})
        self.assertNotIn("output_config", out)

    def test_thinking_false_disables_thinking(self):
        prov = make_prov(reasoning_effort=None, thinking=False)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertEqual(out.get("thinking"), {"type": "disabled"})
        self.assertNotIn("output_config", out)

    def test_thinking_false_and_effort_low_still_disables(self):
        # thinking=False 为强约束，即使 effort 给了合法值也以关闭思考为准
        prov = make_prov(reasoning_effort="low", thinking=False)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertEqual(out.get("thinking"), {"type": "disabled"})
        self.assertNotIn("output_config", out)

    def test_effort_low_injects_output_config(self):
        prov = make_prov(reasoning_effort="low", thinking=True)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertNotIn("thinking", out)
        self.assertEqual(out.get("output_config"), {"effort": "low"})

    def test_effort_max_injects_output_config(self):
        prov = make_prov(reasoning_effort="max", thinking=True)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertNotIn("thinking", out)
        self.assertEqual(out.get("output_config"), {"effort": "max"})

    def test_no_effort_and_no_thinking_flag_leaves_plain(self):
        prov = make_prov(reasoning_effort=None, thinking=True)
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertNotIn("thinking", out)
        self.assertNotIn("output_config", out)

    def test_effort_from_capabilities_is_picked_up(self):
        # reasoning_effort 只放在 capabilities 里也应被识别
        prov = make_prov(reasoning_effort=None, thinking=True)
        prov["capabilities"] = {"thinking": True}
        prov["reasoning_effort"] = "none"
        out = MiniMaxAdapter().transform_request(base_request(), prov)
        self.assertEqual(out.get("thinking"), {"type": "disabled"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
