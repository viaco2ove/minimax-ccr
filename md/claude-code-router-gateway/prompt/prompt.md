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