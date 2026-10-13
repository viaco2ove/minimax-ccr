# minimax 模型
https://platform.minimax.cn/docs/api-reference/text-anthropic-api
M2.5、M2.7 系列模型请求将于【2026 年 10 月 19 日】统一路由至 M3.1 Flash Preview。


|`thinking`|完全支持|`MiniMax-M3.1-Flash-Preview` 强制开启 thinking，传入 `disabled` 会返回 `400`。`MiniMax-M3` 默认关闭 thinking，可通过 `adaptive` 开启。M2.x 模型的 thinking 无法关闭。|
|:----|:----|:----|


|`output_config.effort`|完全支持|调节 `MiniMax-M3.1-Flash-Preview` 的思考深度，可取 `low`、`medium`、`high`、`xhigh`、`max`；省略时默认为 `max`。|
|:----|:----|:----|

### Thinking 控制

`thinking` 参数用于控制模型是否可以输出 `thinking` 内容块。不同模型的行为不同：

|模型|省略 `thinking`|`{"type": "adaptive"}`|`{"type": "disabled"}`|
|:----|:----|:----|:----|
|`MiniMax-M3.1-Flash-Preview`|开启 thinking|开启 thinking|**返回`400`**，thinking 无法关闭|
|`MiniMax-M3`|关闭 thinking|开启 thinking|保持 thinking 关闭|
|M2.x|开启 thinking|开启 thinking|被接收但不生效，thinking 仍保持开启|


