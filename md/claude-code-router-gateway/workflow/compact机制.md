# compact  命令的机制问题
/compact  命令的机制问题。
claudecode 现在的版本会后延到/compact  + 一条用户发言。

但是。也就是 /compact  执行后。 用户继续发言被识别为了compact

修复方案
核心思路：区分"纯 /compact 命令"与"compact + 后续真实发言"。
若消息里除了 compact 命令包装块外还残留真实用户文本，说明 compact 已执行且用户又发了言，不应再按 compact 路由。