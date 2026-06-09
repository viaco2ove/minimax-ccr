"""
ccrg_code_runner.py - CCRG Code 自动化执行器

qoder 可以通过这个脚本启动自动化工作流，类似 Codex CLI：
- 自动分析任务
- 多步骤执行
- 循环修复直到成功
- 实时显示进度

用法:
    python ccrg_code_runner.py loop "任务描述" --files file1.py --commands "python test.py"
    python ccrg_code_runner.py plan "任务描述" --files file1.py
    python ccrg_code_runner.py exec "任务描述" --commands "pytest"
"""

import argparse
import json
import sys
import time
import re
import subprocess
from pathlib import Path
from typing import Optional

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
    """给文本添加颜色"""
    return f"{c}{text}{Colors.END}"

def step(msg: str, step_num: int = None, total: int = None):
    """显示步骤信息"""
    prefix = ""
    if step_num:
        if total:
            prefix = color(f"[{step_num}/{total}] ", Colors.CYAN)
        else:
            prefix = color(f"[Step {step_num}] ", Colors.CYAN)
    print(f"\n{prefix}{color(msg, Colors.BOLD)}")

def info(msg: str):
    """显示信息"""
    print(f"  {color('→', Colors.DIM)} {Colors.DIM}{msg}{Colors.END}")

def success(msg: str):
    """显示成功信息"""
    print(f"  {color('✓', Colors.GREEN)} {color(msg, Colors.GREEN)}")

def error(msg: str):
    """显示错误信息"""
    print(f"  {color('✗', Colors.RED)} {color(msg, Colors.RED)}")

def warn(msg: str):
    """显示警告信息"""
    print(f"  {color('⚠', Colors.YELLOW)} {color(msg, Colors.YELLOW)}")

def section(title: str):
    """显示分节标题"""
    print(f"\n{color('='*60, Colors.DIM)}")
    print(color(f"  {title}", Colors.HEADER))
    print(color('='*60, Colors.DIM))

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='CCRG Code 自动化执行器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python ccrg_code_runner.py loop "修复 BUG" --files bug.py --commands "python bug.py"
  python ccrg_code_runner.py plan "添加功能X" --files main.py
  python ccrg_code_runner.py exec "运行测试" --commands "pytest -v"
        '''
    )

    parser.add_argument('task_type', choices=['read', 'write', 'exec', 'loop', 'plan', 'review'],
                        help='任务类型')
    parser.add_argument('task', help='任务描述')
    parser.add_argument('--files', nargs='*', help='文件路径列表')
    parser.add_argument('--commands', nargs='*', help='命令列表')
    parser.add_argument('--context', help='额外上下文')
    parser.add_argument('--model', help='指定模型')
    parser.add_argument('--max-rounds', type=int, default=5, help='loop 最大轮数')
    parser.add_argument('--base-url', default='http://127.0.0.1:3429', help='CCRG 服务地址')
    parser.add_argument('--project-path', help='项目路径')

    return parser.parse_args()

def build_mcp_request(task_type: str, task: str, **kwargs) -> dict:
    """构建 MCP 请求参数"""
    params = {
        "name": "ccrg_code",
        "arguments": {
            "task_type": task_type,
            "task": task
        }
    }

    args = params["arguments"]

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
        "params": params
    }

def call_mcp(request: dict, base_url: str) -> dict:
    """调用 MCP 服务"""
    import urllib.request
    import urllib.error

    url = f"{base_url}/mcp"
    data = json.dumps(request).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))

            # 提取结果
            if 'result' in result:
                content = result['result'].get('content', [])
                for item in content:
                    if item.get('type') == 'text':
                        return {'success': True, 'text': item['text']}
                return {'success': True, 'text': str(result['result'])}
            elif 'error' in result:
                return {'success': False, 'error': result['error']}

            return {'success': False, 'error': 'Unknown response'}

    except urllib.error.URLError as e:
        return {'success': False, 'error': f'连接失败: {e}'}
    except Exception as e:
        return {'success': False, 'error': f'请求失败: {e}'}

def extract_code_blocks(text: str) -> list:
    """从文本中提取代码块"""
    pattern = r'```(?:\w+)?\n(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches]

def write_file(path: str, content: str) -> bool:
    """写入文件"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
        return True
    except Exception as e:
        error(f"写入文件失败 {path}: {e}")
        return False

def run_command(cmd: str, timeout: int = 120) -> tuple:
    """运行命令"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding='utf-8'
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"命令超时 ({timeout}秒)"
    except Exception as e:
        return False, "", str(e)

def analyze_task(task: str) -> dict:
    """分析任务类型和策略"""
    task_lower = task.lower()

    # 检测任务类型
    if any(k in task_lower for k in ['修复', 'fix', 'bug', 'error', '错误', '异常']):
        primary = 'loop'
        strategy = '自动修复循环'
    elif any(k in task_lower for k in ['分析', 'analyze', '检查', '查看']):
        primary = 'read'
        strategy = '分析文件'
    elif any(k in task_lower for k in ['创建', '生成', '编写', '写', 'create', 'write', 'add']):
        primary = 'write'
        strategy = '生成代码'
    elif any(k in task_lower for k in ['规划', '计划', 'plan', 'design']):
        primary = 'plan'
        strategy = '规划任务'
    elif any(k in task_lower for k in ['运行', '执行', 'test', 'run', 'check']):
        primary = 'exec'
        strategy = '执行命令'
    elif any(k in task_lower for k in ['审查', 'review', 'review', '检查']):
        primary = 'review'
        strategy = '审查代码'
    else:
        primary = 'chat'
        strategy = '对话分析'

    return {'primary': primary, 'strategy': strategy}

def run_workflow(args):
    """执行工作流"""
    section("CCRG Code 自动化执行器")

    info(f"任务类型: {args.task_type}")
    info(f"任务描述: {args.task}")

    if args.files:
        info(f"文件: {', '.join(args.files)}")
    if args.commands:
        info(f"命令: {', '.join(args.commands)}")
    if args.max_rounds:
        info(f"最大轮数: {args.max_rounds}")

    print()

    # 构建 MCP 请求
    mcp_req = build_mcp_request(
        args.task_type,
        args.task,
        files=args.files,
        commands=args.commands,
        context=args.context,
        model=args.model,
        max_rounds=args.max_rounds
    )

    # 执行 MCP 调用
    step(f"正在调用 CCRG MCP ({args.task_type})...")

    result = call_mcp(mcp_req, args.base_url)

    if not result.get('success'):
        error(f"MCP 调用失败: {result.get('error')}")
        return 1

    response_text = result.get('text', '')
    # 显示结果（带步骤格式化）
    print(f"\n{color('─'*60, Colors.DIM)}")
    lines = response_text.strip().split('\n')
    for line in lines:
        line = line.rstrip()
        if line.startswith('## ') or line.startswith('### '):
            print(color(line, Colors.CYAN))
        elif line.startswith('```'):
            print(color(line, Colors.DIM))
        elif '✅' in line:
            print(color(line, Colors.GREEN))
        elif '❌' in line:
            print(color(line, Colors.RED))
        else:
            print(line)
    print(f"{color('─'*60, Colors.DIM)}\n")

    return 0

def run_loop_workflow(args):
    """执行 loop 模式自动修复工作流"""
    section("CCRG Code 自动修复循环")

    info(f"任务: {args.task}")
    info(f"最大轮数: {args.max_rounds}")

    if args.commands:
        info(f"验证命令: {args.commands[0] if args.commands else 'N/A'}")

    print()

    context = args.context or ""

    for round_num in range(1, args.max_rounds + 1):
        step(f"Round {round_num}/{args.max_rounds}", step_num=round_num, total=args.max_rounds)

        # 构建请求
        mcp_req = build_mcp_request(
            'loop',
            args.task,
            files=args.files,
            commands=args.commands,
            context=context,
            max_rounds=1  # 每次只执行一轮
        )

        info("正在调用 CCRG MCP...")
        result = call_mcp(mcp_req, args.base_url)

        if not result.get('success'):
            error(f"MCP 调用失败: {result.get('error')}")
            return 1

        response_text = result.get('text', '')
        print(f"\n{color('─'*40, Colors.DIM)}")
        print(response_text)
        print(f"{color('─'*40, Colors.DIM)}\n")

        # 检查是否成功（简单的关键词检测）
        response_lower = response_text.lower()
        if any(k in response_lower for k in ['成功', '完成', '✓', 'passed', 'success', 'done']):
            success("任务完成!")
            return 0

        # 如果还有代码生成，检查是否需要写入
        code_blocks = extract_code_blocks(response_text)
        if code_blocks and args.files:
            info(f"检测到 {len(code_blocks)} 个代码块")
            for i, code in enumerate(code_blocks):
                if i < len(args.files):
                    if write_file(args.files[i], code):
                        success(f"已写入: {args.files[i]}")

        # 更新上下文
        context += f"\n\n--- Round {round_num} ---\n{response_text}"

        # 如果用户按 Ctrl+C 或有特定退出信号则停止
        print()

    warn(f"达到最大轮数 ({args.max_rounds})，停止自动修复")
    return 1

def main():
    """主入口"""
    args = parse_args()

    # 检查 MCP 服务可用性
    try:
        import urllib.request
        req = urllib.request.Request(f"{args.base_url}/mcp")
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except:
        error(f"无法连接到 CCRG 服务: {args.base_url}")
        error("请确保 CCRG 服务正在运行")
        return 1

    if args.task_type == 'loop' and args.commands:
        return run_loop_workflow(args)
    else:
        return run_workflow(args)

if __name__ == '__main__':
    sys.exit(main())