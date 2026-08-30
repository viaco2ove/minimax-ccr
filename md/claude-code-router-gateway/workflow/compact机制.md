# compact  命令的机制问题
/compact  命令的机制问题。
claudecode 现在的版本会后延到/compact  + 一条用户发言。

但是。也就是 /compact  执行后。 用户继续发言被识别为了compact

修复方案
核心思路：区分"纯 /compact 命令"与"compact + 后续真实发言"。
若消息里除了 compact 命令包装块外还残留真实用户文本，说明 compact 已执行且用户又发了言，不应再按 compact 路由。

# workflow_splitter
"workflow_splitter": {
  "enabled": true,
  "active_strategy": "semantic_splitter",
  "semantic_splitter": {
      "type": "local",
      "model_name": "moka-ai/m3e-small",
      "hf_endpoint":"https://hf-mirror.com",
      "trust_remote_code": true
  },
  "llm_splitter": {
      "routes":  ["minimax:MiniMax-M2.7", "minimax_long:MiniMax-M3"],
      "timeout":  10000
  }
}



workflow 改成使用独立的splitter 和独立的agent 和代码去判断是那个workflow. 除了用户发言直接触发intention_analyze， ai 自己的后续要

使用 进行workflow_splitter 进行判断是那个workflow。