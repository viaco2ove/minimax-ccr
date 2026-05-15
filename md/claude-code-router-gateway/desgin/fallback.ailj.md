# Fallback 机制设计

## 核心原则

**一次 CLI 请求，只发一个请求到一个 provider。**

fallback 只在 provider 返回错误（网络错误、超时、400/429 等）时才触发，不在 provider 成功返回时触发。

## 触发条件

只有以下情况才 fallback 到下一个 provider：

| 错误类型 | 是否 fallback | 原因 |
|---------|-------------|------|
| HTTP 429 (Rate Limit) | ✅ | 服务端限流，可换 provider 重试 |
| HTTP 400 (参数错误) | ✅ | 可剥离不支持的功能后重试一次（仅一次） |
| HTTP 5xx (服务端错误) | ✅ | 服务端临时问题，可换 provider 重试 |
| 网络错误/超时 | ✅ | 可能是网络问题，换 provider 重试 |
| HTTP 200 (成功) | ❌ | **成功后立即返回，不继续尝试下一个** |

## 流程图

```
CLI 请求 → CCRG 路由决策 → route_list: [A, B, C]
                                    ↓
                               尝试 provider A
                                    ↓
                         ┌──────────┴──────────┐
                    200 成功               错误/超时
                         ↓                      ↓
                    直接返回              尝试 provider B
                    (不继续)                   ↓
                                        ┌───────┴───────┐
                                   200 成功        错误/超时
                                        ↓              ↓
                                   直接返回       尝试 provider C
                                                       ↓
                                                  ┌────┴────┐
                                              200 成功   错误
                                                   ↓        ↓
                                              直接返回  返回错误给 CLI
```

## 关键实现

```python
for try_route in route_list:
    try:
        chunk_count = 0
        async for chunk in call_provider_streaming(try_route, msgs):
            yield chunk
            chunk_count += 1
            success = True

        # async for 正常结束（无异常）= provider 200 成功
        if success:
            return  # 成功就停，不继续尝试下一个

    except Exception as e:
        # 只有异常才继续下一个
        continue
```

## 不允许的行为

- ❌ provider A 200 成功后，继续尝试 provider B
- ❌ 没有异常但 `chunk_count == 0` 时当作"部分成功"继续
- ❌ 在 `call_provider_streaming` 内部自行切换 provider

## 错误类型标记

最后一个 provider 失败时，根据错误类型返回不同的 error type：

- `rate_limit_exceeded` - HTTP 429
- `context_length_exceeded` - context 超限
- `provider_error` - 其他所有错误