# intention_analyze 截断 嵌入提示词
流程：intention_analyze → execute_solve → analyze_plan → execute_write
- "{workflow_stage:intention_analyze}": 分析用户意图阶段
- "{workflow_stage:execute_solve}"": 执行解决方案阶段
- "{workflow_stage:analyze_plan}"": 分析执行结果阶段
- "{workflow_stage:execute_write}"": 写入结果阶段
返回时：请用 {workflow_stage:execute_solve} 等标识你下一步想继续分析，还是执行解决方案，还是写入结果

# anthropic 协议
``` json
{
  "model": "claude-sonnet-4-5",
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": "流程：intention_analyze → execute_solve → analyze_plan → execute_write\n- \"{workflow_stage:intention_analyze}\": 分析用户意图阶段\n- \"{workflow_stage:execute_solve}\": 执行解决方案阶段\n- \"{workflow_stage:analyze_plan}\": 分析执行结果阶段\n- \"{workflow_stage:execute_write}\": 写入结果阶段\n返回时：请用 {workflow_stage:execute_solve} 等标识你下一步想继续分析，还是执行解决方案，还是写入结果\n\n我的问题是：帮我写一个Python爬虫"
    }
    。。。。。
  ]
}
```

