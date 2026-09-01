两种策略的判定链路相同，只在"如何给输入文本打分"这一步有差异。先看共同入口，再分策略说明。

## 共同判定链路

```
用户发言(is_user_initiated)          → stage = intention_analyze（固定，不走 splitter）
AI 自身后续(工具回调等非用户发言)      → _detect_workflow_stage(body)
                                          └─ _workflow_stage_splitter.detect(body)
                                               └─ RoutingDecision.workflow_stage（核心字段）
                                                    ├─ 有值 → 采用
                                                    └─ 空/异常 → 回退 _infer_stage_from_context
```

关键点：最终阶段不是 splitter 直接说"你是 execute_solve"，而是 splitter 算出 **5 个 category 的分数**（chat_intention / intention_analyze / problem_analyze / solution_plan / execute_solve），取**最高分的那个 category** 查映射表得到 `workflow_stage`：

| 命中 category（分数最高） | workflow_stage |
|---|---|
| chat_intention | chat_intention |
| intention_analyze | intention_analyze |
| problem_analyze | analyze_plan |
| solution_plan | execute_solve |
| execute_solve | execute_solve |

以下分别说两种策略如何产生这 5 个 category 的分数。

---

## 一、`active_strategy: "semantic_splitter"`（本地语义模型）

用本地 embedding 模型 `moka-ai/m3e-small` 做语义相似度打分，全程本地、无 API 费用。

**流程**：

1. 提取输入文本（最后一条 user 消息，剥离 `<system-reminder>` 噪声块）
2. `model.encode(text)` 得到输入向量
3. 对 5 个 category 的每个关键词都做一次 encode，与输入向量算**余弦相似度**
4. 每个 category 取相似度最高的分数 → 得到 `best_scores = {category: 最高分}`
5. 命中规则：分数 ≥ threshold（默认 0.5）才算命中；否则该 category 视为未命中

**判定**（`resolve_workflow_stage(best_scores, threshold=0.5)`）：

- 在 5 个 category 里取最高分的 category，映射为 workflow_stage
- 若最高分 < 0.5（全都不像）→ 返回 None → 回退上下文推断
- 模型加载失败 / 未命中关键词 → `_keyword_fallback` 走关键词规则兜底

**特点**：

- 判定快（本地推理）、无调用成本
- 语义近似能力强：不要求关键词字面出现，"帮我把这段代码优化一下"即使没写"优化"也能贴近 solution_plan/execute_solve 语义
- 依赖模型：首次启动会下载 m3e-small（走 hf-mirror，已支持 `HF_ENDPOINT`），无缓存则启动时预加载

---

## 二、`active_strategy: "llm_splitter"`（LLM 分流）

调用外部大模型（`llm_splitter.routes` 里配置的 `minimax:MiniMax-M2.7` / `minimax_long:MiniMax-M3`）来做意图分类，语义理解能力最强。

**流程**：

1. 提取输入文本
2. 按 `routes` 顺序逐个尝试，把关键词库和输入文本拼进 system prompt，请求模型输出**纯 JSON**：
   ```json
   {"chat_intention":[],"intention_analyze":[],"problem_analyze":[],"solution_plan":[],"execute_solve":[]}
   ```
3. 解析返回 JSON，把每个 category 里的关键词（可带分数）组装成 `matched`
4. 每个 category 取最高分 → 构造 `category_scores`

**判定**（`resolve_workflow_stage(category_scores)`，阈值默认 0.0）：

- 取最高分 category → 映射 workflow_stage
- 首个 route 成功即用其结果；失败自动切换下一个 route
- 所有 route 都失败 → 回退关键词规则（`_keyword_fallback`）

**特点**：

- 理解力最强，能处理复杂、隐晦、多意图混合的表达
- 有网络调用延迟（timeout 可配，默认配置 10000）和 API 消耗
- 对"哪个 category"的判断依赖模型本身，可能不如本地模型的分数可控

---

## 两种策略的差异对比

| 维度 | semantic_splitter | llm_splitter |
|---|---|---|
| 打分主体 | 本地 m3e-small embedding | 外部 LLM（routes 列表） |
| 判定依据 | 余弦相似度 ≥ 0.5 阈值 | 模型输出的 category 关键词 JSON |
| 速度 | 快（本地） | 慢（网络往返） |
| 成本 | 无 | 有 token 消耗 |
| 语义能力 | 语义近似 | 最强 |
| 无命中/失败 | 回退关键词规则 → 上下文推断 | 逐个 route 重试 → 回退关键词规则 → 上下文推断 |
| 适用 | 快速轻量、对延迟敏感 | 追求准确、可接受延迟 |

当前 `.gateway.json` 的 active_strategy 是 `semantic_splitter`；改回 `llm_splitter` 只需改这一项，两套代码均已就绪。