原始日志，不脑补配置、只拿日志实锤证据说话
只统计 success: true 真正完成请求 的有效记录，失败的直接忽略（没干活不算）。
一、三家模型日志行为精准实锤
1. deepseek-reasoner
绝大多数 success: false，大量超时 / 调用失败
仅有的成功请求，全部只有一种路由：route_rule: scenario.think
输入 token 固定 2.4w～2.6w，延迟 3.7s~8.3s
日志里没有：
没有承接 background/keyword_routing/tool_routing 任何其他场景
没有承接超大 3w+、4w+、5w+ 长上下文
结论：
只零星跑通极少部分轻量思考请求，绝大多数推理分析直接失败，根本扛不住核心思考决策。
2. minimax-M2.7
绝大部分 success: false，延迟普遍 4s～15s+，超时严重
所有成功请求，几乎全部固定路由：route_rule: tool_routing.cheap_tasks
全是极小 token、短请求，没有任何 2w+ 大上下文
日志里完全没有：
没有任何 scenario.think 思考推理成功记录
没有 keyword_routing 架构 / 重构 / 决策类请求
没有高耗时、复杂逻辑任务
结论（日志实锤）：
Minimax 只干最简单的轻量工具小任务（读文件、简易 bash），全程不参与任何分析、推理、决策、架构设计、长文本理解。
3. qianfan-code-latest（日志里真正的核心干活方）
路由覆盖最全
成功请求覆盖所有路由规则：
scenario.think 推理思考
scenario.background 后台任务
keyword_routing 关键词架构 / 搜索
scenario.think 兜底 fallback
独占超大长上下文
日志里大量 3w / 4w / 5w+ token 超高输入请求，全部只有千帆能成功跑完，DeepSeek、Minimax 碰到全失败。
承接所有失败兜底
清晰可见规律：
DeepSeek / Minimax 请求失败 → 路由降级 → 千帆接手并成功完成。
高耗时复杂任务全包
日志里延迟 10s～70s 的超长复杂请求，全部是千帆成功处理，另外两家扛不住直接超时失败。
二、日志直白结论（不绕弯）
Minimax：只做无脑轻量工具活，完全不做分析、不做推理、不做决策。
DeepSeek：只偶尔跑几个少量思考请求，大部分推理直接摆烂失败，没承担核心分析决策。
Qianfan（日志实锤）：
所有需要动脑的分析、逻辑推理、方案决策、架构设计、超长上下文解读、复杂任务规划、其他模型失败兜底，全是千帆在实际处理。
三、一句话总结
看日志：
动脑思考、做决策、分析问题、啃大长文本、处理复杂任务，现在完全是千帆一手包办；另外一个只干杂活，一个名义挂名推理实际躺平。