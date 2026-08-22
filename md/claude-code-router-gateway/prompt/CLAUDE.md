- Always reply in Chinese.

很多文档的处理出现了中文乱码。第一要看文档的编码，输出的编码，写完得检查一下是否有乱码！！！
任何问题都要进过联网查证。遇到不理解的需要进行询问。

- 文件尽量不要放到根目录下
* test文件 全部写到tests文件夹下,如果有些测试是临时的测试，需要放到tests/tmp 或者test/xxx/tmp下
* report 文件全部写到report文件夹下
* md 文件全部写到md文件夹下

- 写代码要具有分层思维不要什么代码都写在一个文件夹下
* 最简单的分层是mvc 分层方式
* 第二种分层方式是功能分层。不同的功能放到不同的文件夹下

- 回复的风格
* 回复时不要总是说：完美!xxx

## 不允许进行修改的文件说明
- review_xxx.md 文件 
是用户自己验证功能的文件，ai 不允许进行修改
ai 可以新增或者修改 review_xxx.answer.md

## 不允许ai 修改的标注
文件第一行: @no_modify
或者 # @no_modify
或者 # no_modify

## 不允许随意放置测试和临时文档
测试脚本和文档和临时文档
只允许放置在.cache 文件夹下。

## 技能要求
~/.claude/mmx.conf 
当 `mmx_enable=true` 时可用，使用 MiniMax MMX CLI 实现多种 AI 能力。
其中特别是图像理解能力

## output-styles
你的行为要符合设置的output-styles



## 处理问题的方式
- 表查询出错的第一件事应该是去看看这个表的结构
- 看到一个报错或者问题应该去看看根源问题是什么。而不是暴力解决

## 模型接口
你现在claude code cli 调用大模型，连接了ccrg 模型路由
每次请求的 必须包含 workflow_stage 字段
ccrg 识别:{workflow_stage:analyze_plan}
intention_analyze: 返回{workflow_stage:analyze_plan} 等。
客户端发送内容包含：
- "{workflow_stage:intention_analyze}": 分析用户意图阶段
- "{workflow_stage:execute_solve}"": 执行解决方案阶段
- "{workflow_stage:analyze_plan}"": 分析执行结果阶段
- "{workflow_stage:execute_write}"": 写入结果阶段

流程：intention_analyze → execute_solve → analyze_plan → execute_write