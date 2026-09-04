"""
LLMSplitter — 基于 LLM 模型的工作流意图分流器。

通过配置的 provider:model 调用外部 LLM，分析命中关键词并返回路由。
取代 keyword_routing。
"""

import json
import logging
import re
import threading
import time as _time
from typing import Any

import httpx

from .base import RoutingDecision, Splitter, resolve_workflow_stage
from ..log.log_controller import verbose_log

logger = logging.getLogger("ccrg")


class LLMSplitter(Splitter):
    """使用 LLM 模型分析关键词并返回路由 — 取代 keyword_routing"""

    # Prompt 模板在 __init__ 时用 keywords.llm.json 动态生成
    SYSTEM_PROMPT_TEMPLATE = """你是分流关键词匹配器。
输入: {user_content}

关键词库：
{keywords_text}

输出格式（仅JSON，禁止任何其他文字）：
{{"chat_intention":[],"intention_analyze":[],"problem_analyze":[],"solution_plan":[],"execute_solve":[]}}"""

    USER_PROMPT_TEMPLATE = ""

    def __init__(self, config: dict[str, Any] | None, keywords: dict, registry: Any = None, usage_stats: Any = None):
        super().__init__(usage_stats=usage_stats)
        self.splitter_type = "llm"
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
        # llama-cpp-python 推理参数
        self.gguf_n_ctx = local_cfg.get("n_ctx", 32768)  # 上下文窗口，默认 32K（Qwen3-0.6B 支持）
        self.gguf_n_threads = local_cfg.get("n_threads", 8)  # 推理线程数
        self.gguf_n_threads_batch = local_cfg.get("n_threads_batch", 8)  # 批处理线程数
        self.gguf_n_batch = local_cfg.get("n_batch", 512)  # prefill 批处理大小，越大 prompt 处理越快
        self.gguf_use_mlock = local_cfg.get("mlock", True)  # 锁定内存防止交换，加速明显
        self.gguf_max_tokens = local_cfg.get("max_tokens", 512)  # 推理输出最大 token 数
        # KV cache 量化类型（可显著减少内存占用）
        # type_k: K cache 量化类型（如 "f16", "q8_0", "q4_0", "q3_k", "q5_k"）
        # type_v: V cache 量化类型（同上）
        # 设置后 llama-cpp 会对 KV cache 进行量化，内存占用大幅降低
        self.gguf_type_k = local_cfg.get("type_k", None)
        self.gguf_type_v = local_cfg.get("type_v", None)
        # llama-cpp-python 实例
        self._llama_model = None
        self._llama_load_lock = threading.Lock()  # 防止并发重复加载
        # 安装状态文件路径
        self._state_file = None
        # 活跃的异步安装 Promise（防止重复安装）
        self._install_task = None

        # 加载 keywords.llm.json，动态生成 system_prompt
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent
        llm_keywords_path = project_root / "keywords.llm.json"
        try:
            with open(llm_keywords_path, "r", encoding="utf-8") as f:
                self.keywords_llm = json.load(f)
            logger.info(f"[LLMSplitter] loaded keywords.llm.json from {llm_keywords_path}")
        except Exception:
            self.keywords_llm = self.keywords
            logger.warning(f"[LLMSplitter] keywords.llm.json not found, using keywords.json")

        # 构建 keywords_text
        wf = self.keywords_llm.get("workflow_intent", {})
        lines = []
        for cat, kws in wf.items():
            lines.append(f"- {cat}：{','.join(kws)}")
        keywords_text = "\n".join(lines)
        self.system_prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            keywords_text=keywords_text,
            user_content="[用户输入将在此处替换]",
        )

        self.fallback_splitter: Splitter | None = None

        # 检查是否有本地模型路由
        has_local_route = any(r.startswith("local:") for r in self.routes)
        if has_local_route:
            if self.local_model_run_start:
                logger.info(f"[LLMSplitter] 本地模型路由检测到，GGUF={self.gguf_file_name}，启动时异步安装/预热...")
                # 异步执行，不阻塞服务启动（参考 Toonflow startQwen060OnBoot）
                t = threading.Thread(target=self._start_on_boot, daemon=True)
                t.start()
            else:
                logger.info(f"[LLMSplitter] 本地模型路由检测到，GGUF={self.gguf_file_name}，将在首次请求时加载")
        logger.info(f"[LLMSplitter] configured: routes={self.routes}")

    def detect(self, body: dict) -> RoutingDecision:
        """使用 LLM 分析关键词并返回路由决策"""
        detect_start = _time.time()
        user_content = self._extract_user_text(body)
        if not user_content.strip():
            decision = self._keyword_fallback(body)
            self._record(decision, (_time.time() - detect_start) * 1000)
            return decision

        for route in self.routes:
            start = _time.time()
            is_local = route.startswith("local:")
            logger.info(f"[LLMSplitter] >>> 尝试 route={route}, user_content={user_content[:50]}...")

            try:
                verbose_log("LLMSplitter", "_call_llm start", "LLM_SPLITTER_DEBUG")
                result = self._call_llm(route, user_content)
                elapsed = _time.time() - start
                verbose_log("LLMSplitter", f"_call_llm end, result length={len(result) if result else 0}", "LLM_SPLITTER_DEBUG")

                if result:
                    verbose_log("LLMSplitter", f"result preview: {result[:200] if len(result) > 200 else result}", "LLM_SPLITTER_DEBUG")
                    matched = self._parse_llm_response(result)
                    verbose_log("LLMSplitter", f"parsed matched: {matched}", "LLM_SPLITTER_DEBUG")
                    route_str, fb, intent = self._resolve_route_from_keywords(matched)
                    # 按最高分 category 映射 workflow_stage（llm 用 matched 中每类最高分）
                    category_scores = {
                        cat: max((s for _, s in items), default=0.0)
                        for cat, items in matched.items()
                    }
                    workflow_stage = resolve_workflow_stage(category_scores)
                    # matched 可能是空 dict，也是有效结果
                    logger.info(
                        f"[LLMSplitter] <<< 成功 route={route} | 耗时={elapsed:.1f}s | "
                        f"matched={matched} | intent={intent} | 最终route={route_str} | workflow_stage={workflow_stage}"
                    )
                    decision = RoutingDecision(
                        intent=intent,
                        route=route_str,
                        matched_rule="llm_routing",
                        matched_reason=f"keywords={matched}" if matched else "no_match",
                        fallback=fb,
                        workflow_stage=workflow_stage,
                    )
                    self._record(decision, (_time.time() - detect_start) * 1000)
                    return decision
                logger.info(f"[LLMSplitter] <<< route={route} 返回空结果，继续下一个 | elapsed={elapsed:.1f}s")
            except Exception as e:
                import traceback
                elapsed = _time.time() - start
                logger.warning(f"[LLMSplitter] <<< route={route} 失败: {e} | elapsed={elapsed:.1f}s")
                verbose_log("LLMSplitter", f"{route} failed: {e}\n{traceback.format_exc()}", "LLM_SPLITTER_DEBUG")
                continue

        logger.info("[LLMSplitter] 所有 route 均失败，回退到 keyword fallback")
        verbose_log("LLMSplitter", "all routes failed, using keyword fallback", "LLM_SPLITTER_DEBUG")
        decision = self._keyword_fallback(body)
        self._record(decision, (_time.time() - detect_start) * 1000)
        return decision

    def _parse_llm_response(self, text: str) -> dict:
        """解析 LLM 返回的 JSON，直接返回 categories -> [(kw, score)] 结构，对齐 semantic_splitter"""
        text = text.strip()
        logger.info(f"[LLMSplitter] _parse_llm_response: {text[:300]}")
        # 提取 JSON 对象
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            logger.warning(f"[LLMSplitter] 无 JSON，尝试直接解析为数组")
            # 尝试解析为纯数组：["kw1", "kw2"] → chat_intention:[(kw1,1.0),(kw2,1.0)]
            try:
                arr = json.loads(text)
                if isinstance(arr, list):
                    return {"chat_intention": [(kw, 1.0) if isinstance(kw, str) else kw for kw in arr]}
            except Exception:
                pass
            return {}

        try:
            data = json.loads(json_match.group())
            result = {}
            for cat, val in data.items():
                if not isinstance(val, list):
                    continue
                items = []
                for item in val:
                    if isinstance(item, str):
                        items.append((item, 1.0))
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        items.append((str(item[0]), float(item[1])))
                if items:
                    result[cat] = items
            logger.info(f"[LLMSplitter] parsed matched: {result}")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"[LLMSplitter] JSON 解析失败: {e}")
            return {}

    def _resolve_route_from_keywords(self, matched: dict) -> tuple[str, list[str] | None, str]:
        """根据命中的关键词从 keyword_routing.rules 找路由，对齐 semantic_splitter 风格"""
        rules = self.config.get("routing", {}).get("keyword_routing", {}).get("rules", [])

        # 打印带分数的命中结果，按每个 category 最高分降序
        if matched:
            sorted_matched = sorted(
                matched.items(),
                key=lambda x: max(s for _, s in x[1]) if x[1] else 0,
                reverse=True,
            )
            log_items = [f"{cat}:{kws}" for cat, kws in sorted_matched]
            logger.info(f"[LLMSplitter] matched Arr: {{{', '.join(log_items)}}}")

        # 提取纯关键词列表（用于匹配 rules）
        def extract_kws(cat_matched):
            """从 [(kw, score), ...] 或 [kw, ...] 中提取关键词列表"""
            kws = []
            for item in cat_matched:
                if isinstance(item, (list, tuple)):
                    kws.append(item[0])
                else:
                    kws.append(item)
            return kws

        chat_matched = matched.get("chat_intention", [])
        task_matched = matched.get("intention_analyze", [])
        chat_kws = extract_kws(chat_matched)
        task_kws = extract_kws(task_matched)

        # 按命中数量判断 intent
        if len(task_kws) > len(chat_kws):
            intent = "task"
            matched_kws = task_kws
        else:
            intent = "chat"
            matched_kws = chat_kws if chat_kws else task_kws

        # 用关键词去 rules 里匹配
        for rule in rules:
            rule_kws = rule.get("keywords", [])
            if any(kw in rule_kws for kw in matched_kws):
                route = rule.get("route", "")
                fb = rule.get("fallback", [])
                logger.info(f"[LLMSplitter] 命中 rule keywords={matched_kws} -> route={route}")
                return route, fb if fb else None, intent

        default = self.config.get("routing", {}).get("default", "minimax:MiniMax-M2.7")
        logger.info(f"[LLMSplitter] 未命中任何 rule，使用 default route={default}")
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

        self._install_task = threading.Thread(target=do_install, daemon=True)
        self._install_task.start()
        return self._install_task

    def _load_llama_model(self):
        """加载 GGUF 模型到内存（参考 Toonflow ensureModelLoaded，加锁防止并发重复加载）"""
        if self._llama_model is not None:
            return

        with self._llama_load_lock:
            # double-check：抢到锁后再确认一次
            if self._llama_model is not None:
                return

            model_file = self._get_model_file_path()
            import os
            if not os.path.exists(model_file):
                raise RuntimeError(f"GGUF 模型文件不存在: {model_file}，请先运行安装")

            logger.info(f"[LLMSplitter] 正在加载 GGUF 模型: {model_file}...")
            start = _time.time()

            try:
                from llama_cpp import Llama
                llama_kwargs = dict(
                    model_path=model_file,
                    n_ctx=self.gguf_n_ctx,
                    n_threads=self.gguf_n_threads,
                    n_threads_batch=self.gguf_n_threads_batch,
                    n_batch=self.gguf_n_batch,
                    use_mmap=True,
                    use_mlock=self.gguf_use_mlock,
                    verbose=False,
                )
                # KV cache 量化（需要 llama-cpp-python >= 0.2.60）
                # type_k/type_v 需要 GGML 整数枚举值，将字符串映射为常量
                ggml_type_map = {
                    "f32": 0, "f16": 1,
                    "q4_0": 2, "q4_1": 3,
                    "q5_0": 6, "q5_1": 7,
                    "q8_0": 8,
                    "q8_1": 9,
                    "q2_k": 10, "q3_k": 11, "q4_k": 12, "q5_k": 13, "q6_k": 14, "q8_k": 15,
                }
                if self.gguf_type_k:
                    tk = ggml_type_map.get(str(self.gguf_type_k).lower())
                    if tk is not None:
                        llama_kwargs["type_k"] = tk
                if self.gguf_type_v:
                    tv = ggml_type_map.get(str(self.gguf_type_v).lower())
                    if tv is not None:
                        llama_kwargs["type_v"] = tv
                logger.info(f"[LLMSplitter] KV cache量化: type_k={self.gguf_type_k}, type_v={self.gguf_type_v}")
                self._llama_model = Llama(**llama_kwargs)
                elapsed = _time.time() - start
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
        status = self._get_install_status()
        if status["status"] != "installed":
            raise RuntimeError(f"Qwen3-0.6B 未安装: {status['message']}，请先安装")

        self._load_llama_model()

        # 构建 messages（system prompt 包含用户输入，用 /no_think 禁用思考）
        prompt = self.system_prompt.replace("[用户输入将在此处替换]", user_content)
        chat_messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "/no_think"},
        ]

        verbose_log("LLMSplitter", f"[local:gguf] messages: {chat_messages}", "LLM_SPLITTER_DEBUG")

        start = _time.time()
        resp = self._llama_model.create_chat_completion(
            messages=chat_messages,
            max_tokens=self.gguf_max_tokens,
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
        """构建 messages 数组，用户输入替换进 system prompt"""
        prompt = self.system_prompt.replace("[用户输入将在此处替换]", user_content)
        return [
            {"role": "system", "content": prompt},
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