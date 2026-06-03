# Gateway 配置案例说明

本目录收集了不同 Provider 组合和路由策略的配置案例，用于快速切换和参考。

## 文件清单

| 文件名 | 主要 Provider | 推荐场景 |
|---|---|---|
| `.gateway.exsample.json` | minimax + qianfan | **最简 4 provider**（无 doubao/deepseek/小米的入门配置） |
| `.gateway.exsample.v2.json` | minimax + doubao + qianfan | v2 baseline（3 平台，semantic_splitter 启用） |
| `.gateway.qf_dou.json` | qianfan + doubao 主，minimax 兜底 | qianfan 优先（stable 模式） |
| `.gateway.dou_q_m.json` | minimax + doubao + qianfan | minimax 默认（high-context 128K） |
| `.gateway.mimo_fq_m.json` | xiaomi + qianfan + minimax | **小米 mimo 主力 + 4 provider 完整链** |
| `.gateway.mimo_m3.json` | xiaomi + minimax-M3 | **小米 + MiniMax-M3 优先，minimax 高速版主力** |
| `.gateway.m3.json` | xiaomi + minimax-M3 | minimax-M3 作为 highspeed 兜底 |
| `.gateway.json` | minimax + qianfan + doubao | 基础 5 provider 模板（端口 3429） |

---

## 1. `.gateway.exsample.json` — 最简入门

**Provider**：minimax + qianfan + mmx + deepseek（**没有 doubao/小米**）

**特点**：
- 端口 3428，最少 provider 数量
- 4 个场景路由 + 3 个 tool 路由 + 2 个 keyword 路由
- 默认路由：`minimax:MiniMax-M2.7`（直白）
- `max_context` 都设为 128K（宽松）

**适用**：第一次跑通 CCRG、不想配太多 key 的场景。

---

## 2. `.gateway.exsample.v2.json` — v2 Baseline

**Provider**：minimax + doubao + qianfan + mmx + deepseek

**特点**：
- 端口 3428
- 5 个 provider 完整版
- **启用了 `semantic_splitter`**（intfloat/multilingual-e5-small）
- 5 个场景路由（背景/思考/长上下文/搜索/图片）+ 3 个 tool 路由 + 2 个 keyword 路由
- 限额监控：minimax_codeplan

**适用**：标准 5 provider 环境，作为后续 v3/v4 的对比 baseline。

---

## 3. `.gateway.qf_dou.json` — qianfan 优先（stable 模式）

**Provider**：minimax + doubao + qianfan + mmx + deepseek

**特点**：
- **默认路由：`qianfan:qianfan-code-latest`**（百度千帆第一）
- `splitter.active_strategy: llm_splitter`（用 LLM 做意图分流）
- `cheap_tasks` / `standard_tasks` 走 minimax
- `complex_tasks` 走 doubao
- 限额耗尽 fallback 到 qianfan

**适用**：希望**百度千帆**做主路由（稳定性强），其他作为兜底的场景。

---

## 4. `.gateway.dou_q_m.json` — minimax 默认 + 128K context

**Provider**：minimax + doubao + qianfan + mmx + deepseek

**特点**：
- 端口 3428
- **`max_context` 全部改为 128K**（v2 还是 32K）
- 默认路由：`minimax:MiniMax-M2.7`
- `llm_splitter` 启用
- workflow 各阶段 fallback 链更长

**适用**：处理长对话上下文、需要 128K context 的场景。

---

## 5. `.gateway.mimo_fq_m.json` — 小米 mimo 主力（4 provider 完整链）

**Provider**：xiaomi + qianfan + minimax + doubao + mmx + deepseek

**特点**：
- 端口 3428
- **加入小米 mimo**：3 个模型（`mimo-v2.5-pro` / `mimo-v2.5` / `mimo-v2-omni`）
- `providers_adapter: "xiaomi"`（**用 `api-key` header 鉴权，不是 `Authorization: Bearer`**）
- **image 路由：qianfan:kimi-k2.5**（kimi 支持图片）
- `keyword_routing` 命中关键词时优先 `xiaomi:mimo-v2.5-pro`
- 启用了 `HF_ENDPOINT=https://hf-mirror.com` 解决 huggingface 访问问题

**适用**：日常主力配置，**平衡了成本和稳定性**。

---

## 6. `.gateway.mimo_m3.json` — 小米 + MiniMax-M3 优先

**Provider**：xiaomi + minimax（多模型：M2.7 / M2.7-highspeed / M3）

**特点**：
- **新增 `MiniMax-M3` 和 `MiniMax-M2.7-highspeed` 模型**
- 默认路由：`minimax:MiniMax-M2.7-highspeed`（高速版）
- `think` / `long_context` / `image` / `complex_tasks` 全部优先 `minimax:MiniMax-M3`（更强）
- `compact` 优先 `minimax:MiniMax-M2.7-highspeed`（便宜）

**适用**：使用最新 MiniMax-M3 模型、想要速度 vs 能力权衡的场景。

---

## 7. `.gateway.m3.json` — minimax-M3 作为 highspeed 兜底

**Provider**：xiaomi + minimax + doubao + qianfan + mmx + deepseek

**特点**：
- 与 `mimo_m3` 类似但 `default` 路由不同
- 默认 `minimax:MiniMax-M2.7-highspeed`
- `think` / `long_context` 优先 `minimax:MiniMax-M3`
- `image` 优先 `minimax:MiniMax-M3`（图片能力）

**适用**：以高速版本为主、M3 兜底复杂任务的场景。

---

## 8. `.gateway.json` — 基础 5 provider 模板

**Provider**：minimax + qianfan + doubao + mmx + deepseek（**没有小米**）

**特点**：
- 端口 3429（调试端口）
- 没有 `splitter` 配置（默认 keyword_splitter）
- 路由策略标准

**适用**：开发测试用（端口 3429 与运行端口 3428 区分）。

---

## ⭐ 推荐方案

### 日常使用：`.gateway.mimo_fq_m.json`（小米 + 4 provider 完整链）

**理由**：
1. **小米 mimo 性价比高**：`mimo-v2.5` / `mimo-v2.5-pro` 比 MiniMax-M2.7 便宜，质量接近
2. **图片走 kimi-k2.5**（支持 vision），xiaomi 不支持图片
3. **完整 fallback 链**：xiaomi → qianfan → doubao → minimax，任意一个挂了都能切
4. **HF_ENDPOINT 配置**避免 huggingface 访问问题
5. **包含 `mmx`** 处理本地/图形理解

### 想要最新模型：`.gateway.mimo_m3.json`（小米 + MiniMax-M3）

**理由**：
1. 加入了 **MiniMax-M3**（最新最强）
2. 高速版 (`M2.7-highspeed`) 做默认路由（响应快）
3. M3 处理复杂任务（think/image/long_context）
4. **价格阶梯**：高速版 < M3 < 复杂任务按需升级

### 入门测试：`.gateway.exsample.json`（4 provider 最简版）

**理由**：
1. provider 数量最少，配 key 最快
2. 没有 doubao/小米/复杂路由
3. 128K max_context 适合大多数场景

---

## Provider 特性对比

| Provider | 价格 | 速度 | thinking | vision | 图片支持 | 鉴权方式 |
|---|---|---|---|---|---|---|
| `minimax:MiniMax-M2.7` | 便宜 | 中 | ✓ | ✓ | ✓ | `Authorization: Bearer` |
| `minimax:MiniMax-M2.7-highspeed` | 便宜 | **快** | ✓ | ✓ | ✓ | `Authorization: Bearer` |
| `minimax:MiniMax-M3` | 中 | 中 | ✓ | ✓ | ✓ | `Authorization: Bearer` |
| `xiaomi:mimo-v2.5` | **便宜** | 中 | ✓ | ✗ | ✗ | `api-key: <token>` |
| `xiaomi:mimo-v2.5-pro` | 中 | 中 | ✓ | ✗ | ✗ | `api-key: <token>` |
| `qianfan:qianfan-code-latest` | 便宜 | 中 | ✓ | ✓ | ✓ | `Authorization: Bearer` |
| `qianfan:kimi-k2.5` | 便宜 | 中 | ✓ | ✓ | **✓** | `Authorization: Bearer` |
| `doubao:ark-code-latest` | 便宜 | 中 | ✓ | ✓ | ✓ | `Authorization: Bearer` |
| `doubao:doubao-seed-2.0-pro` | 中 | 中 | ✓ | ✓ | **✓** | `Authorization: Bearer` |
| `mmx:MiniMax-M2.7` | 本地 | 极快 | ✓ | ✓ | ✓ | `Authorization: Bearer` |

---

## 切换方法

```bash
# 复制某个配置到项目根目录
cp gateway_exsample/.gateway.mimo_fq_m.json .gateway.json

# 重启 CCRG 服务
python -m src.ccrg.main
```

Dashboard 端点：`http://127.0.0.1:3428/dashboard`（或 3429 取决于端口）
