# mmx
先运行
[run.mmx_p.bat](../../run.mmx_p.bat)

# 机制
    1 CCR → https://127.0.0.1:3457/v1/messages → mmx_provider.py (本地服务) → mmx CLI 命令
   api_base_url 不能删，它是必须的：
   - https://127.0.0.1:3457 是 mmx_provider.py 监听的本地服务
   - mmx_provider.py 接收 HTTP 请求后，调用 mmx CLI 命令（或直接调用 API）
   - 这样设计的好处是：CCR 不需要知道 mmx 的具体调用方式，通过标准 HTTP 接口通信

   所以 .gateway.json 里的配置是正确的：
    1 "mmx": {
    2   "api_base_url": "https://127.0.0.1:3457",  // ← 必须保留
    3   "protocol": "mmx",
    4   ...
    5 }
   总结：
   - mmx 走的是 HTTP API（https://127.0.0.1:3457），不是直接命令调用
   - api_base_url 不能删，它是 CCR 与 mmx_provider 之间的通信地址
   - mmx_provider.py 是中间层，负责把 HTTP 请求转为 mmx CLI/API 调用
