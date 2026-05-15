# 编写 cli req 请求预处理器
req_cli_pre.py
[400_空字符.md](../../error/400_%E7%A9%BA%E5%AD%97%E7%AC%A6.md)
判断是否有空值
"content": [
  {"type":"text","text":""},  // 👇 这8个空值，全是CLI续传时加的占位符
  {"type":"text","text":""},
  ...
  // 后面才是真正的会话内容
]

判断是否json -》是否有空值-》清除空值-》发给大模型