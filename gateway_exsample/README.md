# Gateway 配置案例说明

本目录收集了不同 Provider 组合和路由策略的配置案例，用于快速切换和参考。

> **⚠️ 计费提醒**：`MiniMax-M2.7-highspeed` 费用是 `MiniMax-M2.7` 的 **2 倍**，除非对延迟有硬性要求，不建议在日常路由中使用 highspeed 版。以下所有推荐方案均以降低套餐消耗为目标。

---
脱敏处理：
```
" api_key.*
->
"api_key":"xxxx",
```

## 文件清单

| 文件名 | 主要 Provider | 推荐场景                      |
|---|---|---------------------------|
| `.gateway.mimo_fq_m.json` | xiaomi + qianfan + minimax | ⭐ **推荐：日常主力（最低成本）**       |
| `.gateway.mimo_m3.json` | xiaomi + minimax-M3 | ⭐ **推荐：需要 M3 能力时**        |
| `.gateway.dou_q_m.json` | minimax + doubao + qianfan | minimax 默认 + 128K context |
| `.gateway.qf_dou.json` | qianfan + doubao + minimax | 百度千帆优先（稳定性强）              |
| `.gateway.m3.json` | xiaomi + minimax-M3 | M3 作为主力                   |
| `.gateway.exsample.v2.json` | minimax + doubao + qianfan | v2 baseline（3 平台）         |
| `.gateway.json` | minimax + qianfan + doubao | 开发测试（端口 3429）             |
| `.gateway.exsample.json` | minimax + qianfan | 最简 4 provider 入门          |
| `.gateway.m3_main.mimo_small.json` | xiaomi + minimax-M3 | M3 作为主力                   |
| `.gateway.mimo_main.m3_small.json` | xiaomi + minimax-M3 | xiaomi 作为主力               |

---

## ⭐ 推荐方案

### 第一推荐：`.gateway.mimo_fq_m.json`（小米 mimo 主力 + 4 provider 完整链）

**Provider**：xiaomi + qianfan + minimax + doubao + mmx + deepseek

**为什么推荐**：
1. **小米 mimo 性价比最高**：`mimo-v2.5` / `mimo-v2.5-pro` 比 MiniMax-M2.7 便宜，质量接近
2. **全部用 M2.7 标准版**，不使用 highspeed，**计费减半**
3. **图片走 kimi-k2.5**（支持 vision），xiaomi 不支持图片
4. **完整 fallback 链**：xiaomi → qianfan → doubao → minimax，任意一个挂了都能切
5. 启用 `HF_ENDPOINT=https://hf-mirror.com` 解决 huggingface 访问问题
6. 限额耗尽自动 fallback 到 qianfan

**路由分配**：
- 默认 / cheap_tasks / standard_tasks / compact → `minimax:MiniMax-M2.7`
- think / complex_tasks / 关键词命中 → `xiaomi:mimo-v2.5-pro`
- image → `qianfan:kimi-k2.5`（支持图片）
- 意图分析 → `xiaomi:mimo-v2.5-pro` → minimax → qianfan

---

### 第二推荐：`.gateway.mimo_m3.json`（小米 + MiniMax-M3）

**Provider**：xiaomi + minimax（M2.7 / M3）+ qianfan + doubao + mmx

**什么时候选这个**：
- 需要 MiniMax-M3 的更强能力时（复杂推理、长上下文）
- M2.7 无法满足的 think / image / long_context 场景

**路由分配**：
- 默认 / cheap_tasks / standard_tasks / compact → `minimax:MiniMax-M2.7`（标准版，不加价）
- think / long_context / image / complex_tasks → `minimax:MiniMax-M3`（按需升级）
- 关键词命中 → `xiaomi:mimo-v2.5-pro`

**成本控制**：只在真正需要 M3 能力的场景才调用 M3，日常任务走标准 M2.7。

---

## 各配置详细对比

### `.gateway.mimo_fq_m.json` — ⭐ 日常主力配置

**Provider**：xiaomi + qianfan + minimax + doubao + mmx + deepseek（6 个，完整）

| 特性 | 详情 |
|---|---|
| 默认路由 | `minimax:MiniMax-M2.7` |
| image 路由 | `qianfan:kimi-k2.5`（支持 vision） |
| think 路由 | `xiaomi:mimo-v2.5-pro` |
| compact 路由 | `minimax:MiniMax-M2.7` |
| splitter | `semantic_splitter`（moka-ai/m3e-small + hf-mirror） |
| quota fallback | `qianfan:qianfan-code-latest` |
| **费用** | **最低**（全部标准版 M2.7，不使用 highspeed/M3） |

---

### `.gateway.mimo_m3.json` — M3 按需升级

**Provider**：xiaomi + minimax（M2.7 + M3）+ qianfan + doubao + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 默认路由 | `minimax:MiniMax-M2.7` |
| think / long_context / image | `minimax:MiniMax-M3` |
| complex_tasks | `minimax:MiniMax-M3` |
| cheap_tasks / standard_tasks | `minimax:MiniMax-M2.7` |
| **费用** | **中等**（只在 M3 场景加价，日常走标准 M2.7） |

> **注意**：此文件已将 highspeed 全部替换为标准 M2.7，节省约 50% 费用。

---

### `.gateway.m3.json` — M3 全面替代

**Provider**：xiaomi + minimax（M2.7 + M3）+ doubao + qianfan + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 默认路由 | `minimax:MiniMax-M2.7` |
| think / long_context / image | `minimax:MiniMax-M3` |
| **费用** | 中等（M3 场景按需） |

> **注意**：此文件已将 highspeed 全部替换为标准 M2.7。

---

### `.gateway.dou_q_m.json` — 128K context 专用

**Provider**：minimax + doubao + qianfan + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 默认路由 | `minimax:MiniMax-M2.7` |
| max_context | **全部 128K**（标准版只有 32K） |
| splitter | `llm_splitter` |
| **费用** | 低（无 highspeed/M3/小米） |

**适用**：处理超长对话上下文、代码库分析等大 context 场景。

---

### `.gateway.qf_dou.json` — 百度千帆优先

**Provider**：minimax + doubao + qianfan + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 默认路由 | `qianfan:qianfan-code-latest` |
| splitter | `llm_splitter` |
| cheap_tasks / standard_tasks | `minimax:MiniMax-M2.7` |
| complex_tasks | `doubao:ark-code-latest` |
| **费用** | 低 |

**适用**：百度千帆配额充足、想要稳定性的场景。

---

### `.gateway.exsample.v2.json` — v2 baseline

**Provider**：minimax + doubao + qianfan + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 默认路由 | `minimax:MiniMax-M2.7` |
| splitter | `semantic_splitter`（intfloat/multilingual-e5-small） |
| **费用** | 低 |

**适用**：作为对比 baseline，测试不同路由策略的效果差异。

---

### `.gateway.json` — 开发测试

**Provider**：minimax + qianfan + doubao + mmx + deepseek

| 特性 | 详情 |
|---|---|
| 端口 | **3429**（与运行端口 3428 区分） |
| splitter | 默认 keyword_splitter |
| **费用** | 低 |

---

### `.gateway.exsample.json` — 最简入门

**Provider**：minimax + qianfan + mmx + deepseek（**4 个，最少**）

| 特性 | 详情 |
|---|---|
| max_context | 全部 128K |
| **费用** | 最低 |

**适用**：第一次跑通 CCRG、不想配太多 key 的场景。

---

## Provider 特性对比

| Provider | 价格 | 速度 | thinking | vision | 鉴权方式 |
|---|---|---|---|---|---|
| `minimax:MiniMax-M2.7` | 便宜 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `minimax:MiniMax-M3` | 中 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `xiaomi:mimo-v2.5` | **便宜** | 中 | ✓ | ✗ | `api-key: <token>` |
| `xiaomi:mimo-v2.5-pro` | 中 | 中 | ✓ | ✗ | `api-key: <token>` |
| `qianfan:qianfan-code-latest` | 便宜 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `qianfan:kimi-k2.5` | 便宜 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `doubao:ark-code-latest` | 便宜 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `doubao:doubao-seed-2.0-pro` | 中 | 中 | ✓ | ✓ | `Authorization: Bearer` |
| `mmx:MiniMax-M2.7` | 本地 | 极快 | ✓ | ✓ | `Authorization: Bearer` |

> **不推荐**：`minimax:MiniMax-M2.7-highspeed` — 费用是标准版的 **2 倍**，除非对延迟有硬性要求（如实时交互），否则用标准 M2.7 即可。

---

## 费用估算参考

假设每月 10 万次请求（按平均 token 计算）：

| 方案 | 主力模型 | 相对费用 |
|---|---|---|
| 全 highspeed | M2.7-highspeed | **200%** |
| M2.7 标准 + M3 按需 | M2.7 + M3（10%） | **110%** |
| 全 M2.7 标准 | M2.7 | **100%**（基准） |
| 小米 mimo 为主 | mimo-v2.5 | **60-80%** |

---

## 切换方法

```bash
# 复制某个配置到项目根目录
cp gateway_exsample/.gateway.mimo_fq_m.json .gateway.json

# 重启 CCRG 服务
python -m src.ccrg.main
```

Dashboard：`http://127.0.0.1:3428/dashboard`（或 3429，取决于端口配置）
