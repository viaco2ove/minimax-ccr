# no_modify
ai 的一坨：[fallback.ailj.md](fallback.ailj.md)
# 先看gateway.json
```json
  "route": "doubao:ark-code-latest",
  "fallback": ["qianfan:qianfan-code-latest","minimax:MiniMax-M2.7"]
```

```json
"analyze_plan": ["doubao:ark-code-latest","qianfan:qianfan-code-latest"],
```

## 首先写个 FallbackRouter 类
fallback_router.py 
FallbackRouter 装载routeList
```json
  "route": "doubao:ark-code-latest",
  "fallback": ["qianfan:qianfan-code-latest","minimax:MiniMax-M2.7"]
```
装载routeList= ["doubao:ark-code-latest","qianfan:qianfan-code-latest","minimax:MiniMax-M2.7"]
```json
"analyze_plan": ["doubao:ark-code-latest","qianfan:qianfan-code-latest"],
```
装载routeList= ["doubao:ark-code-latest","qianfan:qianfan-code-latest"]

###  getRouteList() 方法

### main.py 调用getRouteList ， debug 时 打印 什么类型的命中：
```
[keyword] {keyword}
[workflow] {workflowName}
etc...
```
[FallbackRouter] [RouteList]: [HitType] {HitType}
[FallbackRouter] [RouteList]: {RouteList}

### route 被main.py 使用后
 debug 时 打印 :
[FallbackRouter] CurrRoute [index] {index}  [routeName] {routeName}

[FallbackRouter] [ReqCleanEmpty] 清理空字符 {"type": "text", "text": ""}...
[ReqCleanEmpty.md](ReqCleanEmpty.md)

[FallbackRouter] [REQ] [CURL] curl 请求体

route.req(....)

[FallbackRouter] [RESULT] [STATUS] {STATUS}

[FallbackRouter]  [RESULT] [REPONSE] {REPONSE}

[FallbackRouter] [CHECK_RESULT] [NEET_NEXT] {boolean} [WHY] {why}