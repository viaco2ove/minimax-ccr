"""
ccrg_code_watcher.py - CCRG Code SSE 流式执行器

通过 SSE 流式接收 MCP 每一步的返回，实时显示给用户。
类似 Claude Code 的自动修复循环：
- SSE 实时接收每一步
- 自动检测失败并继续下一轮
- 生成修复代码并自动执行
- 收到 done:true 或达到 max_rounds 时停止

用法:
    python ccrg_code_watcher.py --task "修复 validate.py 的错误" \
        --files script/validate.py \
        --commands "python script/validate.py" \
        --max-rounds 5
"""

import argparse
import json
import sys
import time
import threading
import queue
from pathlib import Path

# ANSI 颜色代码
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'

def color(text: str, c: str) -> str:
    return f"{c}{text}{Colors.END}"

def step(msg: str, step_num: int = None, total: int = None):
    if step_num:
        if total:
            prefix = color(f"[{step_num}/{total}] ", Colors.CYAN)
        else:
            prefix = color(f"[Step {step_num}] ", Colors.CYAN)
    else:
        prefix = color("→ ", Colors.CYAN)
    print(f"\n{prefix}{color(msg, Colors.BOLD)}")

def info(msg: str):
    print(f"  {Colors.DIM}{msg}{Colors.END}")

def success(msg: str):
    print(f"  {color('✓', Colors.GREEN)} {color(msg, Colors.GREEN)}")

def error(msg: str):
    print(f"  {color('✗', Colors.RED)} {color(msg, Colors.RED)}")

def warn(msg: str):
    print(f"  {color('⚠', Colors.YELLOW)} {color(msg, Colors.YELLOW)}")

def banner(text: str):
    print(color(f"\n╔{'═'*58}╗", Colors.DIM))
    print(color(f"║{text:^58}║", Colors.BOLD))
    print(color(f"╚{'═'*58}╝", Colors.DIM))

def parse_args():
    parser = argparse.ArgumentParser(description='CCRG Code SSE 流式执行器')
    parser.add_argument('--task', required=True, help='任务描述')
    parser.add_argument('--files', nargs='*', help='文件路径列表')
    parser.add_argument('--commands', nargs='*', help='命令列表')
    parser.add_argument('--context', help='额外上下文')
    parser.add_argument('--model', help='指定模型')
    parser.add_argument('--max-rounds', type=int, default=5, help='最大轮数')
    parser.add_argument('--base-url', default='http://127.0.0.1:3429', help='CCRG 服务地址')
    parser.add_argument('--no-sse', action='store_true', help='不使用 SSE，普通轮询')
    return parser.parse_args()

def build_request(task: str, task_type: str, **kwargs) -> dict:
    args = {"task_type": task_type, "task": task}
    if kwargs.get('files'):
        args["files"] = kwargs['files']
    if kwargs.get('commands'):
        args["commands"] = kwargs['commands']
    if kwargs.get('context'):
        args["context"] = kwargs['context']
    if kwargs.get('model'):
        args["model"] = kwargs['model']
    if kwargs.get('max_rounds'):
        args["max_rounds"] = kwargs['max_rounds']
    return {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": "tools/call",
        "params": {"name": "ccrg_code", "arguments": args}
    }

def stream_sse(base_url: str, step_queue: queue.Queue, stop_event: threading.Event):
    """SSE 线程：监听 SSE 事件，推送到 step_queue"""
    try:
        import urllib.request
        req = urllib.request.Request(f"{base_url}/mcp/sse")
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                if stop_event.is_set():
                    break
                line = line.decode('utf-8').rstrip()
                if line.startswith('event:'):
                    continue
                if line.startswith('data: '):
                    data = line[6:]
                    try:
                        event = json.loads(data)
                        step_queue.put(event)
                    except Exception:
                        pass
    except Exception as e:
        step_queue.put({"type": "error", "text": str(e)})

def send_request(base_url: str, session_id: str, request: dict):
    """发送 MCP 请求"""
    import urllib.request
    url = f"{base_url}/mcp/messages?sessionId={session_id}"
    data = json.dumps(request).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

def format_step(text: str):
    """格式化步骤文本，高亮显示"""
    lines = text.split('\n')
    output = []
    for line in lines:
        line = line.rstrip()
        if not line:
            output.append("")
        elif line.startswith('## ') or '### ' in line:
            output.append(color(line, Colors.CYAN))
        elif line.startswith('**') and line.endswith('**'):
            output.append(color(line, Colors.BOLD))
        elif '✅' in line:
            output.append(color(line, Colors.GREEN))
        elif '❌' in line:
            output.append(color(line, Colors.RED))
        elif '⚠' in line:
            output.append(color(line, Colors.YELLOW))
        elif line.startswith('`') and '`' in line[1:]:
            output.append(color(line, Colors.DIM))
        elif line.startswith('```'):
            output.append(color(line, Colors.DIM))
        else:
            output.append(line)
    return '\n'.join(output)

def main():
    args = parse_args()
    banner("CCRG Code 流式执行器")
    print()
    info(f"任务: {args.task}")
    info(f"最大轮数: {args.max_rounds}")
    if args.files:
        info(f"文件: {args.files}")
    if args.commands:
        info(f"命令: {args.commands}")
    print()

    step_queue = queue.Queue()
    stop_event = threading.Event()

    # 启动 SSE 线程
    sse_thread = threading.Thread(
        target=stream_sse,
        args=(args.base_url, step_queue, stop_event),
        daemon=True
    )
    sse_thread.start()

    # 等待 endpoint 事件
    session_id = None
    for _ in range(30):
        try:
            event = step_queue.get(timeout=5)
            if event.get('type') == 'message' and 'sessionId=' in event.get('data', ''):
                data = event.get('data', '')
                import re
                m = re.search(r'sessionId=([^&\n]+)', data)
                if m:
                    session_id = m.group(1)
                    break
        except queue.Empty:
            continue

    if not session_id:
        error("无法获取 SSE session ID")
        return 1

    success(f"SSE 会话已建立: {session_id[:8]}...")
    print()

    # 构建请求
    mcp_req = build_request(
        args.task,
        'loop',
        files=args.files,
        commands=args.commands,
        context=args.context,
        model=args.model,
        max_rounds=args.max_rounds
    )

    step("发送 ccrg_code(loop) 请求...")
    result = send_request(args.base_url, session_id, mcp_req)
    if result.get('status') == 'accepted':
        info("请求已接受，开始接收 SSE 流...")
    else:
        error(f"请求失败: {result}")
        return 1

    print()

    # 实时显示 SSE 事件
    done = False
    step_count = 0
    while not stop_event.is_set():
        try:
            event = step_queue.get(timeout=300)
        except queue.Empty:
            warn("SSE 超时，连接可能已断开")
            break

        if event.get('type') == 'error':
            error(f"SSE 错误: {event.get('text')}")
            continue

        if event.get('type') == 'message':
            msg_data = event.get('data', {})
            if isinstance(msg_data, dict):
                step_type = msg_data.get('type', '')
                step_text = msg_data.get('text', '')
                is_done = msg_data.get('done', False)

                if step_text:
                    step_count += 1
                    # 实时打印（逐字可能有延迟，批量打印更稳）
                    print(format_step(step_text))
                    if step_count % 10 == 0:
                        sys.stdout.flush()

                if is_done:
                    done = True
                    break
            elif isinstance(msg_data, str):
                try:
                    parsed = json.loads(msg_data)
                    step_text = parsed.get('text', '')
                    is_done = parsed.get('done', False)
                    if step_text:
                        step_count += 1
                        print(format_step(step_text))
                    if is_done:
                        done = True
                        break
                except Exception:
                    pass

    stop_event.set()

    print()
    if done:
        success("任务完成!")
        return 0
    else:
        warn("连接已断开")
        return 1

if __name__ == '__main__':
    sys.exit(main())
