"""
LLMSplitter — 基于 LLM 模型的工作流意图分流器。

通过配置的 provider:model 调用外部 LLM，分析命中关键词并返回路由。
取代 keyword_routing。
"""

import json
import logging
import re
from typing import Any

import httpx

from .base import RoutingDecision, Splitter
from ..log.log_controller import verbose_log

logger = logging.getLogger("ccrg")


class LLMSplitter(Splitter):
    """使用 LLM 模型分析关键词并返回路由 — 取代 keyword_routing"""

    SYSTEM_PROMPT = """你是请求分流关键词匹配器，**仅输出纯JSON，禁止任何多余文字、解释、备注**。按给定关键词库精准匹配，命中就填入对应数组，无匹配字段直接省略。输出格式严格遵循示例：{"workflow_intent":{"chat_intention":["咋样"]}}
关键词库：{keywords_json}"""

    USER_PROMPT_TEMPLATE = """{user_content}

<instruction>
作为模型分流器，请根据上文提供的关键词列表，分析用户的 user_query 命中了哪些关键词。
必须严格且仅输出 JSON 格式数据，不要包含任何思考过程、不要使用 Markdown 代码块（如 ```json）、不要有任何其他自然语言废话。
示例格式：
{{
  "workflow_intent": {{
    "intention_analyze": ["帮我"]
  }},
  "task_routing": {{
    "cheap_tasks": ["查看"]
  }}
}}
</instruction>"""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        self.config = config or {}
        self.keywords = keywords
        self.registry = registry
        self.usage_stats = usage_stats

        splitter_cfg = self.config.get("routing", {}).get("splitter", {})
        llm_cfg = splitter_cfg.get("llm_splitter", {})

        if isinstance(llm_cfg, list):
            self.routes: list[str] = llm_cfg
        else:
            self.routes: list[str] = llm_cfg.get("routes", ["minimax:MiniMax-M2.7"])
        self.timeout = llm_cfg.get("timeout", 10.0) if isinstance(llm_cfg, dict) else 10.0

        # 本地模型配置（GGUF + llama-cpp-python），参考 Toonflow localQwen060.ts
        local_cfg = splitter_cfg.get("local_model", {})
        self.local_model_name = local_cfg.get("model_name", "Qwen3-0.6B")
        self.local_model_run_start = local_cfg.get("local_chat_model_run_start", False)
        # GGUF 下载源（优先级：ModelScope > hf-mirror > huggingface）
        self.gguf_file_name = local_cfg.get("gguf_file_name", "Qwen3-0.6B-Q8_0.gguf")
        self.gguf_model_scope_url = local_cfg.get("model_scope_url", "https://www.modelscope.cn/models/Qwen/Qwen3-0.6B-GGUF/resolve/master/Qwen3-0.6B-Q8_0.gguf")
        self.gguf_hf_mirror_url = local_cfg.get("hf_mirror_url", "https://hf-mirror.com/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf")
        self.gguf_hf_url = local_cfg.get("hf_url", "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-Q8_0.gguf")
        self.gguf_download_headers = local_cfg.get("download_headers", {}) or {}
        # GGUF 存储路径（项目根目录下的 local_models/）
        self.gguf_local_dir = local_cfg.get("local_dir", "local_models")
        # llama-cpp-python 实例
        self._llama_model = None
        # 安装状态文件路径
        self._state_file = None
        # 活跃的异步安装 Promise（防止重复安装）
        self._install_task = None

        # 组装 system prompt，替换 {keywords_json}
        keywords_str = json.dumps(keywords, ensure_ascii=False)
        self.system_prompt = self.SYSTEM_PROMPT.replace("{keywords_json}", keywords_str)

        self.fallback_splitter: Splitter | None = None

        # 检查是否有本地模型路由
        has_local_route = any(r.startswith("local:") for r in self.routes)
        if has_local_route:
            if self.local_model_run_start:
                logger.info(f"[LLMSplitter] 本地模型路由检测到，GGUF={self.gguf_file_name}，启动时异步安装/预热...")
                # 异步执行，不阻塞服务启动（参考 Toonflow startQwen060OnBoot）
                import threading
                t = threading.Thread(target=self._start_on_boot, daemon=True)
                t.start()
            else:
                logger.info(f"[LLMSplitter] 本地模型路由检测到，GGUF={self.gguf_file_name}，将在首次请求时加载")
        logger.info(f"[LLMSplitter] configured: routes={self.routes}")

    def detect(self, body: dict) -> RoutingDecision:
        """使用 LLM 分析关键词并返回路由决策"""
        user_content = self._extract_user_text(body)
        if not user_content.strip():
            return self._keyword_fallback(body)

        for route in self.routes:
            try:
                verbose_log("LLMSplitter", "_call_llm start", "LLM_SPLITTER_DEBUG")
                result = self._call_llm(route, user_content)
                verbose_log("LLMSplitter", f"_call_llm end, result length={len(result) if result else 0}", "LLM_SPLITTER_DEBUG")
                if result:
                    verbose_log("LLMSplitter", f"result preview: {result[:200] if len(result) > 200 else result}", "LLM_SPLITTER_DEBUG")
                    matched = self._parse_llm_response(result)
                    verbose_log("LLMSplitter", f"parsed matched: {matched}", "LLM_SPLITTER_DEBUG")
                    # matched 可能是空 dict，也是有效结果（没命中任何 workflow 关键词）
                    route_str, fb, intent = self._resolve_route_from_keywords(matched)
                    return RoutingDecision(
                        intent=intent,
                        route=route_str,
                        matched_rule="llm_routing",
                        matched_reason=f"keywords={matched}" if matched else "no_match",
                        fallback=fb,
                    )
                verbose_log("LLMSplitter", f"{route} returned empty result", "LLM_SPLITTER_DEBUG")
            except Exception as e:
                import traceback
                verbose_log("LLMSplitter", f"{route} failed: {e}\n{traceback.format_exc()}", "LLM_SPLITTER_DEBUG")
                continue

        verbose_log("LLMSplitter", "all routes failed, using keyword fallback", "LLM_SPLITTER_DEBUG")
        return self._keyword_fallback(body)

    def _parse_llm_response(self, text: str) -> dict:
        """解析 LLM 返回的 JSON，返回 workflow_intent（可能为空 dict）"""
        text = text.strip()
        verbose_log("LLMSplitter", f"_parse_llm_response input: {text[:300] if len(text) > 300 else text}", "LLM_SPLITTER_DEBUG")
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            verbose_log("LLMSplitter", "no JSON found in response", "LLM_SPLITTER_DEBUG")
            return {}
        try:
            data = json.loads(json_match.group())
            verbose_log("LLMSplitter", f"parsed JSON keys: {list(data.keys())}", "LLM_SPLITTER_DEBUG")
            result = data.get("workflow_intent", {})
            verbose_log("LLMSplitter", f"workflow_intent: {result}", "LLM_SPLITTER_DEBUG")
            return result if result else {}
        except json.JSONDecodeError as e:
            verbose_log("LLMSplitter", f"JSON parse error: {e}", "LLM_SPLITTER_DEBUG")
            return {}

    def _resolve_route_from_keywords(self, matched: dict) -> tuple[str, list[str] | None, str]:
        """根据命中的关键词从 keyword_routing.rules 找路由"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        chat_matched = matched.get("chat_intention", [])
        task_matched = matched.get("intention_analyze", [])

        if len(task_matched) > len(chat_matched):
            intent = "task"
            matched_kws = task_matched
        else:
            intent = "chat"
            matched_kws = chat_matched if chat_matched else task_matched

        for rule in rules:
            rule_kws = rule.get("keywords", [])
            if any(kw in rule_kws for kw in matched_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                return route, fb if fb else None, intent

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        return default, None, intent

    def _call_llm(self, route: str, user_content: str) -> str:
        """调用单个 LLM route，返回原始文本"""
        # 检查是否是本地模型路由
        if route.startswith("local:"):
            return self._call_local_model(user_content)

        if ":" not in route:
            raise ValueError(f"Invalid route format: {route}")

        provider, model = route.split(":", 1)
        prov_config = self.registry.get(provider) if self.registry else None

        if not prov_config:
            return self._call_direct(provider, model, user_content)

        return self._call_via_registry(provider, model, user_content, prov_config)

    def _get_model_dir(self) -> str:
        """获取 GGUF 模型存储目录"""
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent
        return project_root / self.gguf_local_dir

    def _get_model_file_path(self) -> str:
        """获取 GGUF 模型文件完整路径"""
        return str(self._get_model_dir() / self.gguf_file_name)

    def _get_state_file_path(self) -> str:
        """获取安装状态文件路径"""
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent
        return str(project_root / self.gguf_local_dir / "install-state.json")

    def _read_state(self) -> dict | None:
        """读取安装状态"""
        import json as _json
        from pathlib import Path
        state_file = self._get_state_file_path()
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return None

    def _write_state(self, state: dict):
        """写入安装状态"""
        import json as _json
        from pathlib import Path
        state_file = self._get_state_file_path()
        Path(state_file).parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            _json.dump(state, f, ensure_ascii=False, indent=2)

    def _get_install_status(self) -> dict:
        """获取 Qwen060 安装状态（参考 Toonflow getQwen060InstallStatus）"""
        model_file_path = self._get_model_file_path()
        import os as _os
        model_exists = _os.path.exists(model_file_path)
        state = self._read_state()
        status = state.get("status") if state else None

        if status == "installing":
            return {"status": "installing", "installed": False, "canInstall": False,
                    "message": state.get("message", "安装中..."), "progressPercent": state.get("progressPercent")}
        if status == "failed":
            return {"status": "failed", "installed": False, "canInstall": True,
                    "message": state.get("lastError", "安装失败")}
        if status == "installed" and model_exists:
            return {"status": "installed", "installed": True, "canInstall": True, "message": "Qwen3-0.6B 已安装"}
        return {"status": "not_installed", "installed": False, "canInstall": True,
                "message": "Qwen3-0.6B 尚未安装"}

    def _download_gguf(self, on_progress: callable = None):
        """下载 GGUF 模型文件（参考 Toonflow downloadModel）"""
        import os as _os
        from pathlib import Path

        model_dir = self._get_model_dir()
        model_file = self._get_model_file_path()
        Path(model_dir).mkdir(parents=True, exist_ok=True)

        if _os.path.exists(model_file):
            on_progress and on_progress("GGUF 模型文件已存在，跳过下载")
            return

        on_progress and on_progress(f"正在下载 Qwen3-0.6B GGUF 模型...")

        # 优先级：ModelScope（国内最稳定）→ hf-mirror → huggingface
        sources = [
            ("ModelScope（国内）", self.gguf_model_scope_url),
            ("HuggingFace 镜像", self.gguf_hf_mirror_url),
            ("HuggingFace 官方", self.gguf_hf_url),
        ]

        last_error = None
        for name, url in sources:
            try:
                on_progress and on_progress(f"从 {name} 下载...")
                self._download_file_stream(url, model_file, on_progress)
                on_progress and on_progress(f"模型文件下载完成（来源：{name}）")
                return
            except Exception as e:
                last_error = e
                on_progress and on_progress(f"{name} 下载失败：{e}，尝试下一个源...")
                try:
                    _os.remove(model_file)
                except Exception:
                    pass

        raise RuntimeError(
            f"所有下载源均失败: {last_error}\n"
            f"请手动下载到: {model_file}\n"
            + "\n".join(f"- {n}: {u}" for n, u in sources)
        )

    def _download_file_stream(self, url: str, dest: str, on_progress: callable = None):
        """流式下载文件（参考 Toonflow downloadFile）"""
        import httpx

        headers = {"User-Agent": "ccrg/1.0"}
        headers.update(self.gguf_download_headers)

        with httpx.Client(timeout=600.0, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0) or 0)
                downloaded = 0
                last_report = 0

                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and downloaded - last_report > 1024 * 1024:  # 每 MB 报告一次
                            mb = downloaded / 1024 / 1024
                            total_mb = total / 1024 / 1024
                            pct = int(downloaded * 100 / total)
                            on_progress and on_progress(f"下载中... {mb:.1f}MB / {total_mb:.1f}MB ({pct}%)", pct)
                            last_report = downloaded

    def _install(self, on_progress: callable = None):
        """安装入口：下载 GGUF 模型（参考 Toonflow installQwen060）"""
        if self._install_task is not None:
            return self._install_task

        def do_install():
            try:
                self._write_state({"status": "installing", "message": "开始安装...", "startedAt": 0})
                on_progress and on_progress("步骤 1/1: 下载 GGUF 模型")
                self._download_gguf(on_progress)
                self._write_state({"status": "installed", "message": "Qwen3-0.6B 已安装", "version": "Qwen3-0.6B-Q8_0"})
                on_progress and on_progress("Qwen3-0.6B 安装完成！")
            except Exception as e:
                import traceback
                self._write_state({"status": "failed", "lastError": str(e)})
                on_progress and on_progress(f"安装失败: {e}")
                raise
            finally:
                self._install_task = None

        import threading
        self._install_task = threading.Thread(target=do_install, daemon=True)
        self._install_task.start()
        return self._install_task

    def _load_llama_model(self):
        """加载 GGUF 模型到内存（参考 Toonflow ensureModelLoaded）"""
        if self._llama_model is not None:
            return

        model_file = self._get_model_file_path()
        import os
        if not os.path.exists(model_file):
            raise RuntimeError(f"GGUF 模型文件不存在: {model_file}，请先运行安装")

        logger.info(f"[LLMSplitter] 正在加载 GGUF 模型: {model_file}...")
        import time
        start = time.time()

        try:
            from llama_cpp import Llama
            self._llama_model = Llama(
                model_path=model_file,
                n_ctx=4096,
                n_threads=4,
                use_mmap=True,
                use_mlock=False,
                verbose=False,
            )
            elapsed = time.time() - start
            logger.info(f"[LLMSplitter] GGUF 模型加载完成，耗时 {elapsed:.1f}s")
        except ImportError:
            raise RuntimeError(
                "llama-cpp-python 未安装，请运行: pip install llama-cpp-python\n"
                "加速安装: pip install llama-cpp-python --force-reinstall --no-cache-dir"
            )

    def _start_on_boot(self):
        """启动时自动安装/预热（参考 Toonflow startQwen060OnBoot）"""
        status = self._get_install_status()
        logger.info(f"[qwen3-0.6b][boot] 启动检查 status={status['status']} installed={status['installed']}")

        if not status["installed"]:
            logger.info(f"[qwen3-0.6b][boot] 检测到未安装，自动开始安装（异步进行）")
            self._install(lambda msg, pct=None: logger.info(f"[qwen3-0.6b][boot] {msg}"))
        else:
            logger.info(f"[qwen3-0.6b][boot] 已安装，预热加载模型...")
            try:
                self._load_llama_model()
                logger.info(f"[qwen3-0.6b][boot] 模型已加载到内存，可立即推理")
            except Exception as e:
                logger.error(f"[qwen3-0.6b][boot] 模型预热失败: {e}")

    def _call_local_model(self, user_content: str) -> str:
        """调用本地 GGUF LLM 模型（llama-cpp-python，参考 Toonflow chatWithQwen060）"""
        import time as _time

        status = self._get_install_status()
        if status["status"] != "installed":
            raise RuntimeError(f"Qwen3-0.6B 未安装: {status['message']}，请先安装")

        self._load_llama_model()

        messages = self._build_messages(user_content)
        system_text = messages[0]["content"][0]["text"]
        user_text = messages[1]["content"][0]["text"]

        # 构建 messages 格式（禁用思考模式 /no_think）
        chat_messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": f"{user_text}\n/no_think"},
        ]

        verbose_log("LLMSplitter", f"[local:gguf] messages: {chat_messages}", "LLM_SPLITTER_DEBUG")

        start = _time.time()
        resp = self._llama_model.create_chat_completion(
            messages=chat_messages,
            max_tokens=512,
            temperature=0.3,
            stop=["<|end_of_text|>", "<|reserved_200|>"],
        )
        elapsed = _time.time() - start

        # 提取文本响应
        choices = resp.get("choices", [])
        content = ""
        if choices:
            raw = choices[0].get("message", {}).get("content", "") or ""
            # Qwen3 可能输出 <think>...</think> 标签，意图分类不需要思考，尝试清洗
            clean = raw.strip()
            import re as _re
            think_match = _re.search(r"</think>\s*([\s\S]*)$", clean)
            if think_match:
                clean = think_match.group(1).strip()
            content = clean

        verbose_log("LLMSplitter", f"[local:gguf] response ({elapsed:.1f}s): {content[:200]}", "LLM_SPLITTER_DEBUG")
        return content

    def _build_messages(self, user_content: str) -> list:
        """构建 messages 数组，符合 Anthropic 格式"""
        return [
            {
                "role": "system",
                "content": [{"type": "text", "text": self.system_prompt}]
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": self.USER_PROMPT_TEMPLATE.format(user_content=user_content)}]
            }
        ]

    def _call_via_registry(self, provider: str, model: str, user_content: str, prov_config: Any) -> str:
        adapter = self._get_adapter_for_provider(provider, prov_config)
        api_base = getattr(prov_config, "api_base_url", "")
        api_key = getattr(prov_config, "api_key", "")
        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        prov_dict = {
            "api_base_url": api_base,
            "protocol": getattr(prov_config, "protocol", ""),
            "providers_adapter": getattr(prov_config, "providers_adapter", ""),
        }
        target_url = adapter.get_target_url(prov_dict, model)

        verbose_log("LLMSplitter", "_build_messages start", "LLM_SPLITTER_DEBUG")
        messages = self._build_messages(user_content)
        verbose_log("LLMSplitter", "_build_messages end", "LLM_SPLITTER_DEBUG")
        req_body = {
            "model": model,
            "messages": messages,
            "max_tokens": 500,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        # 输出 curl 命令
        import shlex
        curl_cmd = f"curl -X POST {shlex.quote(target_url)} "
        for k, v in headers.items():
            curl_cmd += f"-H {shlex.quote(f'{k}: {v}')} "
        curl_cmd += f"-d {shlex.quote(json.dumps(req_body, ensure_ascii=False))}"
        verbose_log("LLMSplitter", f"[curl]\n{curl_cmd}", "LLM_SPLITTER_CURL")

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # 记录 usage
        self._record_usage(provider, model, data)

        content = data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def _call_direct(self, provider: str, model: str, user_content: str) -> str:
        providers = self.config.get("providers", {})
        prov_data = providers.get(provider)
        if not prov_data:
            raise ValueError(f"Provider {provider} not found in config")

        api_base = prov_data.get("api_base_url", "")
        api_key = prov_data.get("api_key", "")
        if not api_base or not api_key:
            raise ValueError(f"Provider {provider} missing api_base or api_key")

        adapter = self._get_adapter_for_provider(provider, prov_data)
        target_url = adapter.get_target_url(prov_data, model)

        messages = self._build_messages(user_content)

        req_body = {
            "model": model,
            "messages": messages,
            "max_tokens": 500,
            "stream": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
        }

        # 输出 curl 命令
        import shlex
        curl_cmd = f"curl -X POST {shlex.quote(target_url)} "
        for k, v in headers.items():
            curl_cmd += f"-H {shlex.quote(f'{k}: {v}')} "
        curl_cmd += f"-d {shlex.quote(json.dumps(req_body, ensure_ascii=False))}"
        verbose_log("LLMSplitter", f"[curl]\n{curl_cmd}", "LLM_SPLITTER_CURL")

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(target_url, json=req_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # 记录 usage
        self._record_usage(provider, model, data)

        content = data.get("content", [])
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
        return ""

    def _record_usage(self, provider: str, model: str, resp_data: dict):
        """记录 LLM 调用消耗到 usage_stats"""
        if not self.usage_stats:
            return
        usage = resp_data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        self.usage_stats.record(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=0,
            success=True,
            route_rule="llm_splitter",
        )

    def _get_adapter_for_provider(self, provider_name: str, prov_config: Any):
        adapter_name = getattr(prov_config, "providers_adapter", "") or getattr(prov_config, "protocol", "")
        if adapter_name == "minimax":
            from ..protocol.minimax_adapter import MiniMaxAdapter
            return MiniMaxAdapter()
        elif adapter_name == "openai":
            from ..protocol.openai_adapter import OpenAIAdapter
            return OpenAIAdapter()
        else:
            from ..protocol.anthropic_adapter import AnthropicAdapter
            return AnthropicAdapter()

    def _keyword_fallback(self, body: dict) -> RoutingDecision:
        from .keyword import KeywordSplitter
        k = KeywordSplitter(config=self.config, keywords=self.keywords)
        return k.detect(body)

    def _extract_user_text(self, body: dict) -> str:
        texts = []
        for msg in body.get("messages", []):
            if msg.get("role") != "user":
                continue
            c = msg.get("content", "")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text":
                        texts.append(b.get("text", ""))
        return " ".join(texts)