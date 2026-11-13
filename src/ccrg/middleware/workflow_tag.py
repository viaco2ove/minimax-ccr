"""Workflow 阶段标记的输出过滤与会话状态缓存。

历史背景：CCRG 曾通过在响应流末尾注入可见的 ``{workflow_stage:xxx}`` 文本 tag，
依赖客户端（ZCode / Claude Code）把对话历史原样回传来传递阶段状态。
该方式的代价是 tag 会直接显示在客户端界面上。

现方案：
- 阶段状态改由服务端 :class:`StageSessionCache` 持有（见 main.py 的
  ``workflow_stream_generator``：阶段完成后写入 next_stage，下一轮工具回调链优先读取）；
- :class:`WorkflowTagStripper` 在 SSE 流层面剥离上游模型可能残留输出的
  workflow_stage 标记（兜底，处理跨 SSE 事件边界的碎片）。
"""

import hashlib
import json
import re
from collections import OrderedDict

# 匹配 workflow_stage 标记，容忍模型乱写的变体：
# {workflow_stage:execute_write} / {workflow_stage=execute_solve}
# [workflow_stage:execute_solve} / { workflow_stage : xxx }
# 旧网关注入形态为独立行 "\n\n{workflow_stage:xxx}"，前置双换行一并吞掉，
# 避免剥离后界面残留空行。
WORKFLOW_TAG_RE = re.compile(
    r"(?:\n\n)?[{[]\s*workflow_stage\s*[:=]\s*\w+\s*[}\]]",
    re.IGNORECASE,
)

# 未闭合标记前缀（跨 SSE 事件边界时前半截先到，需暂存等待后续片段）
_TAG_PREFIX_RE = re.compile(
    r"[{[]\s*workflow_stage\s*[:=]?\s*\w{0,32}\s*[}\]]?$",
    re.IGNORECASE,
)
# 疑似前缀起点：{ / [ 或旧网关注入形态的前置双换行 + { / [
_TAG_START_RE = re.compile(r"(?:\n\n)?[{[]")

# 标记最长形态 {workflow_stage:intention_analyze} 约 34 字符，缓冲窗口留足余量
_MAX_TAG_LEN = 64


def _take_tag_prefix_tail(text: str) -> str:
    """返回 text 尾部疑似未闭合标记前缀的子串；不是前缀则返回空串。

    仅检查末尾 _MAX_TAG_LEN 窗口内的疑似起点（``{`` / ``[``，
    含旧网关注入形态的前置双换行）。从左到右第一个命中即返回（最长暂存）。
    """
    window = text[-_MAX_TAG_LEN:]
    for m in _TAG_START_RE.finditer(window):
        seg = window[m.start():]
        if _TAG_PREFIX_RE.search(seg):
            return seg
    return ""


class WorkflowTagStripper:
    """SSE 流式剥离 workflow_stage 标记。

    工作在事件层面：缓冲不完整的 SSE 事件（以 ``\\n\\n`` 结尾），
    对 ``content_block_delta`` / ``text_delta`` 事件的文本做标记剥离
    （含跨事件边界），其余事件原样透传。内部任何异常均 fail-open：
    放行原始数据，绝不因过滤器故障中断响应流。
    """

    def __init__(self) -> None:
        self._buf = b""          # 不完整 SSE 事件缓冲
        self._text_tail = ""     # 疑似标记前缀的暂存文本

    # ── 对外接口 ──────────────────────────────────────────────

    def feed(self, chunk: bytes) -> bytes:
        """喂入一段上游 SSE 字节流，返回剥离标记后可安全下发的字节。"""
        try:
            self._buf += chunk
            out = []
            while True:
                idx = self._buf.find(b"\n\n")
                if idx < 0:
                    break
                event = self._buf[: idx + 2]
                self._buf = self._buf[idx + 2:]
                out.append(self._process_event(event))
            return b"".join(out)
        except Exception:
            # fail-open：异常时透传原始数据
            self._buf = b""
            self._text_tail = ""
            return chunk

    def flush(self) -> bytes:
        """流结束时调用：放出残留事件与暂存的疑似前缀文本。"""
        try:
            rest = self._buf
            self._buf = b""
            tail = self._text_tail
            self._text_tail = ""
            parts = [rest]
            if tail:
                # 暂存文本最终未凑成完整标记，按普通文本 delta 放出
                event = {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": tail},
                }
                parts.append(
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
                )
            return b"".join(parts)
        except Exception:
            self._buf = b""
            self._text_tail = ""
            return b""

    # ── 内部实现 ──────────────────────────────────────────────

    def _process_event(self, event: bytes) -> bytes:
        stripped = event.strip()
        if not stripped.startswith(b"data:"):
            return event
        payload = stripped[5:].strip()
        if not payload.startswith(b"{"):
            # data: [DONE] 等
            return event
        try:
            obj = json.loads(payload)
        except Exception:
            return event
        if not isinstance(obj, dict):
            return event
        if obj.get("type") != "content_block_delta":
            return event
        delta = obj.get("delta")
        if not isinstance(delta, dict) or delta.get("type") != "text_delta":
            return event

        new_text = self._strip_text(delta.get("text", ""))
        if not new_text:
            # 整个 delta 都是标记，直接丢弃该事件
            return b""
        delta["text"] = new_text
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")

    def _strip_text(self, text: str) -> str:
        buf = self._text_tail + text
        self._text_tail = ""
        out = []
        pos = 0
        for m in WORKFLOW_TAG_RE.finditer(buf):
            out.append(buf[pos:m.start()])
            pos = m.end()
        rest = buf[pos:]
        tail = _take_tag_prefix_tail(rest)
        if tail:
            self._text_tail = tail
            rest = rest[: len(rest) - len(tail)]
        out.append(rest)
        return "".join(out)


class StageSessionCache:
    """会话级 workflow 阶段缓存（LRU）。

    替代"向响应流注入可见 tag、依赖客户端回传历史"的旧方案：
    阶段完成后由网关写入 next_stage，下一轮工具回调链优先读取。

    会话标识取首条 user 消息的**纯文本** hash。

    注意点（来自真实 ZCode 流量验证）：
    - 同一会话内，首条 user 消息的载体形式会变化：首轮是纯字符串，
      后续轮次客户端回传时变成内容块列表（list of blocks）。
      因此必须统一抽取文本内容，不能直接 hash 原始结构。
    - 首轮首条 user 可能被网关前置了 intention_analyze 的 workflow_prompt，
      后续轮次客户端回传的是不含该 prompt 的原始内容，两者文本不同。
      需要剥离该 prompt 后再 hash，否则首轮写入的缓存后续读不到。
    - /compact 压缩历史后首条 user 变化 → miss，自动回退 splitter /
      上下文推断链，可接受。
    """

    # 与 main.py 中 intention_analyze 阶段前置的 workflow_prompt 对应，
    # 用于从首条 user 文本中剥离，避免首轮与后续轮次 key 不一致。
    _PROMPT_PREFIX = (
        "流程：intention_analyze → execute_solve → analyze_plan → execute_write\n"
        "当前处于 intention_analyze（分析用户意图）阶段，完成分析后直接给出结论即可。\n"
        "阶段切换由系统自动完成，不要在回复中输出 {workflow_stage:xxx} 之类的标记。\n\n"
    )

    def __init__(self, maxsize: int = 512) -> None:
        self._data: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize

    @staticmethod
    def _first_user_text(body: dict) -> str:
        """提取首条 user 消息的纯文本内容（不受 str / list 载体形式影响）。"""
        for msg in body.get("messages", []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        parts.append(blk.get("text", ""))
                return "\n".join(parts)
            return str(content)
        return ""

    @classmethod
    def session_key(cls, body: dict) -> str | None:
        text = cls._first_user_text(body)
        if not text:
            return None
        # 剥离网关前置的 workflow_prompt，使首轮与后续轮次 key 一致
        if text.startswith(cls._PROMPT_PREFIX):
            text = text[len(cls._PROMPT_PREFIX):]
        text = text.strip()
        if not text:
            return None
        digest = hashlib.sha1(text[:2000].encode("utf-8", "ignore")).hexdigest()
        return f"first_user:{digest}"

    def get(self, body: dict) -> str | None:
        key = self.session_key(body)
        if not key:
            return None
        stage = self._data.get(key)
        if stage:
            self._data.move_to_end(key)
        return stage

    def set(self, body: dict, stage: str) -> None:
        key = self.session_key(body)
        if not key:
            return
        self._data[key] = stage
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)
