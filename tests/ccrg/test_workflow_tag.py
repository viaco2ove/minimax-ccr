"""WorkflowTagStripper / StageSessionCache 单元测试。

覆盖：
- SSE text_delta 中完整/跨事件边界的 workflow_stage 标记剥离
- 模型乱写变体（=、[...}）剥离
- 正常文本与事件透传
- 流结束时疑似前缀残余放出
- StageSessionCache 读写与 LRU 淘汰
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ccrg.middleware.workflow_tag import (  # noqa: E402
    StageSessionCache,
    WorkflowTagStripper,
)


def sse_text(text: str) -> bytes:
    """构造一个 Anthropic text_delta SSE 事件字节。"""
    event = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


def collect_text(outputs: list[bytes]) -> str:
    """从剥离后的 SSE 输出中提取所有 text_delta 文本。"""
    texts = []
    for out in outputs:
        for line in out.decode("utf-8", "ignore").split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload.startswith("{"):
                continue
            obj = json.loads(payload)
            if obj.get("type") == "content_block_delta":
                texts.append(obj["delta"]["text"])
    return "".join(texts)


def feed_all(stripper: WorkflowTagStripper, data: bytes, size: int = 13) -> list[bytes]:
    """按 size 字节切片喂入（模拟 TCP 分片），返回每次输出。"""
    outs = []
    for i in range(0, len(data), size):
        chunk = stripper.feed(data[i : i + size])
        if chunk:
            outs.append(chunk)
    tail = stripper.flush()
    if tail:
        outs.append(tail)
    return outs


class TestWorkflowTagStripper:
    def test_full_tag_stripped(self):
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("你好 {workflow_stage:execute_write} 世界"))
        assert collect_text(outs) == "你好  世界"

    def test_gateway_style_tag_stripped(self):
        # 模拟旧网关注入形态：\n\n{workflow_stage:xxx} 独立事件
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("回答内容") + sse_text("\n\n{workflow_stage:analyze_plan}"))
        assert collect_text(outs) == "回答内容"

    def test_variant_tags_stripped(self):
        for tag in (
            "{workflow_stage=execute_solve}",
            "[workflow_stage:execute_solve}",
            "{ workflow_stage : intention_analyze }",
            "{WORKFLOW_STAGE:EXECUTE_WRITE}",
        ):
            s = WorkflowTagStripper()
            outs = feed_all(s, sse_text(f"前 {tag} 后"))
            assert collect_text(outs) == "前  后", tag

    def test_tag_across_events(self):
        # 标记被切成两个 text_delta 事件
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("答案{workflow_stage:") + sse_text("execute_solve}完毕"))
        assert collect_text(outs) == "答案完毕"

    def test_tag_across_events_with_blank_lines(self):
        # 旧网关注入形态的 \n\n 前缀与标记主体分属两个事件：前缀也需暂存，
        # 剥离后不得残留空行
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("回答\n\n{workflow_stage:exe") + sse_text("cute_solve}完成"))
        assert collect_text(outs) == "回答完成"

    def test_normal_text_passthrough(self):
        s = WorkflowTagStripper()
        body = "普通回答，含 {dict} 与 [list]，无标记。"
        outs = feed_all(s, sse_text(body))
        assert collect_text(outs) == body

    def test_unclosed_prefix_flushed(self):
        # 正文以疑似前缀结尾且流就此结束：暂存文本必须原样放出，不丢失
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("正文结束 {workflow_"))
        assert collect_text(outs) == "正文结束 {workflow_"

    def test_non_text_events_passthrough(self):
        s = WorkflowTagStripper()
        events = (
            b'data: {"type":"message_start"}\n\n'
            b'data: {"type":"content_block_start","index":0}\n\n'
            b'data: {"type":"content_block_stop","index":0}\n\n'
            b"data: [DONE]\n\n"
        )
        outs = feed_all(s, events)
        assert b"".join(outs) == events

    def test_empty_delta_dropped(self):
        # 整个 delta 就是一个标记 → 事件被丢弃而不是输出空文本
        s = WorkflowTagStripper()
        outs = feed_all(s, sse_text("{workflow_stage:execute_write}"))
        assert collect_text(outs) == ""


class TestStageSessionCache:
    def _body(self, first_user: str) -> dict:
        return {"messages": [{"role": "user", "content": first_user}]}

    def test_roundtrip(self):
        c = StageSessionCache()
        b = self._body("会话一的输入")
        assert c.get(b) is None
        c.set(b, "execute_solve")
        assert c.get(b) == "execute_solve"

    def test_different_sessions_isolated(self):
        c = StageSessionCache()
        c.set(self._body("会话一"), "execute_solve")
        c.set(self._body("会话二"), "analyze_plan")
        assert c.get(self._body("会话一")) == "execute_solve"
        assert c.get(self._body("会话二")) == "analyze_plan"

    def test_list_content_key(self):
        # content 为块列表时同样稳定
        c = StageSessionCache()
        b = {"messages": [{"role": "user", "content": [{"type": "text", "text": "块内容"}]}]}
        c.set(b, "execute_write")
        assert c.get(b) == "execute_write"

    def test_lru_eviction(self):
        c = StageSessionCache(maxsize=2)
        b1, b2, b3 = self._body("a"), self._body("b"), self._body("c")
        c.set(b1, "execute_solve")
        c.set(b2, "execute_solve")
        c.set(b3, "execute_solve")  # 淘汰最久未用的 b1
        assert c.get(b1) is None
        assert c.get(b2) == "execute_solve"
        assert c.get(b3) == "execute_solve"

    def test_no_user_message(self):
        c = StageSessionCache()
        assert c.session_key({"messages": []}) is None
        assert c.get({"messages": []}) is None

    def test_key_stable_across_content_formats(self):
        """真实 ZCode 流量：首轮首条 user 是 str，后续轮次是 block list，
        文本内容相同 → 必须视为同一会话。"""
        c = StageSessionCache()
        text = "用户的第一条原始输入"
        body_str = {"messages": [{"role": "user", "content": text}]}
        body_list = {
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": text}]}
            ]
        }
        c.set(body_str, "execute_solve")
        assert c.get(body_list) == "execute_solve"

    def test_key_stable_with_prompt_prefix(self):
        """首轮首条 user 被网关前置了 workflow_prompt，后续轮次客户端
        回传的是不含 prompt 的原文 → 剥离后 key 必须一致。"""
        c = StageSessionCache()
        prefix = StageSessionCache._PROMPT_PREFIX
        original = "用户的原始提问"
        body_first = {"messages": [{"role": "user", "content": prefix + original}]}
        body_later = {"messages": [{"role": "user", "content": original}]}
        assert c.session_key(body_first) == c.session_key(body_later)
        c.set(body_first, "analyze_plan")
        assert c.get(body_later) == "analyze_plan"
