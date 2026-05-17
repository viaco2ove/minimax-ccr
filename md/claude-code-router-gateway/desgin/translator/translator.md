src/ccrg/translator/openai_translator.py 负责转换
src/ccrg/translator/sse_client.py 负责获取转换中间结果

openai_translator.py<->sse_client.py<->Claude Code Router Gateway (CCRG)

## openai_translator.py 只进行输入输出的处理
## sse_client.py 把输入传入Claude Code Router Gateway (CCRG)
## sse_client.py 把CCRG 传给openai_translator.py
### stream=false
sse_client.py 等到流结束后，再交给openai_translator.py处理
### stream=true
sse_client.py sse 每次返回，都交给openai_translator.py处理

