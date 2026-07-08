# no_modify
## llm_splitter

用 LLM 模型判断意图，精度最高但成本也最高。

## 配置

`.gateway.json`：

```json
"splitter": {
    "active_strategy": "llm_splitter",
    "llm_splitter": {
        "routes": ["minimax:MiniMax-M2.7", "qianfan:qianfan-code-latest"],
        "timeout": 10000
    }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `routes` | provider:model 列表，按顺序尝试。默认 `["minimax:MiniMax-M2.7"]` |
| `timeout` | 单次 LLM 调用超时（秒），默认 10s |

## 工作原理

1. 从 `keywords.json` 提取 `workflow_intent.chat_intention` 和 `intention_analyze` 关键词作为 few-shot 示例
2. 组装 prompt 发送给 LLM
3. LLM 回复 
```
{
  "workflow_intent": {
    "chat_intention": [
      "咋样"
    ]
}
如果返回的不是josn 直接当作分析失败
```
代表命中了keywords.json的什么内容
4. 调用失败则 fallback 到 `keyword_splitter`

## 提示词的设计
```
你是一个模型分流器。分析命中了哪些关键词？用于为claude code cli 的请求分流
## 数据返回约束，json. 
{
  "workflow_intent": {
    "chat_intention": [
      "咋样"
    ]
}
代表命中了什么

## keywords 数据
{keywords.json}

## 入参说明
一般为xml 格式
# 根节点（固定）
<system-reminder data-role="user-context">
  ├─ <user_info>                # 用户环境信息
  │    ├─ OS Version: win32
  │    ├─ Shell: bash
  │    ├─ Workspace Folder: 路径
  │    └─ Note: 路径使用说明
  │
  ├─ <project_context>          # 项目核心上下文
  │    ├─ <project_guidance>    # 项目规范（CDATA包裹）
  │    │    └─ 项目概述/架构/命令/规则/配置
  │    └─ <project_layout>       # 项目文件目录结构
  │
  ├─ <additional_data>           # 附加数据
  │    ├─ <current_time>         # 当前时间
  │    └─ <connector-status>     # 服务连接状态（全disconnected）
  │
  └─ <memory_and_skills_reminder>  # 记忆&技能规则
       └─ 内存写入/技能管理/通用规则

# 重复嵌套结构（交互轮次）
</system-reminder>
<user_query>用户在cli输入的内容</user_query>

```


curl 演示
```
curl --location 'https://api.minimaxi.com/anthropic/v1/messages' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer xxx' \
--header 'anthropic-version: 2023-06-01' \
--data '{
    "model": "MiniMax-M2.7",
    "messages": [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "你是请求分流关键词匹配器，**仅输出纯JSON，禁止任何多余文字、解释、备注**。按给定关键词库精准匹配，命中就填入对应数组，无匹配字段直接省略。输出格式严格遵循示例：{\"workflow_intent\":{\"chat_intention\":[\"咋样\"]}}\n关键词库：{\"workflow_intent\":{\"chat_intention\":[\"你好\",\"在吗\",\"嗨\",\"哈喽\",\"谢谢\",\"辛苦了\",\"闲聊\",\"聊聊天\",\"随便问问\",\"早上好\",\"晚上好\",\"hello\",\"hi\",\"hey\",\"thanks\",\"thank you\",\"咋样\",\"还行吗\",\"没事\",\"随便说\"],\"intention_analyze\":[\"我想\",\"我要\",\"帮我\",\"请问\",\"如何\",\"怎么\",\"能不能\",\"可不可以\",\"需要\",\"想要\",\"希望\",\"需求\",\"目的\",\"目标\",\"意图\",\"打算\",\"计划\"],\"problem_analyze\":[\"分析\",\"排查\",\"诊断\",\"原因\",\"为什么\",\"怎么回事\",\"处理\",\"问题\",\"错误\",\"报错\",\"异常\",\"故障\",\"bug\",\"哪里错\",\"为何\",\"根源\",\"定位\",\"梳理\",\"解析\",\"analyze\",\"diagnose\",\"debug\",\"issue\",\"error\",\"你个垃圾\",\"这他妈\",\"白痴\",\"[deep]\"],\"solution_plan\":[\"方案\",\"解决\",\"办法\",\"思路\",\"步骤\",\"规划\",\"设计\",\"策略\",\"建议\",\"指南\",\"教程\",\"怎么做\",\"方案设计\",\"实施方案\",\"解决方法\",\"计划\",\"solution\",\"plan\",\"strategy\",\"guide\",\"how to\"],\"execute_solve\":[\"执行\",\"实现\",\"动手\",\"运行\",\"操作\",\"处理\",\"修改\",\"编写\",\"生成\",\"创建\",\"修复\",\"搞定\",\"落地\",\"做完\",\"部署\",\"启动\",\"调用\",\"执行命令\",\"execute\",\"run\",\"implement\",\"fix\",\"create\"]},\"task_routing\":{\"cheap_tasks\":[\"查看\",\"读取\",\"搜索\",\"列表\",\"状态\",\"日志\",\"cat\",\"ls\",\"grep\",\"git\",\"head\",\"查看文件\",\"读文件\",\"搜内容\",\"查状态\",\"看日志\"],\"standard_tasks\":[\"编辑\",\"写入\",\"修改\",\"新建\",\"保存\",\"更新\",\"写代码\",\"改代码\",\"编辑文件\",\"write\",\"edit\"],\"complex_tasks\":[\"agent\",\"智能体\",\"任务\",\"规划模式\",\"架构\",\"项目\",\"工程\",\"重构\",\"系统\",\"整体\",\"设计\",\"多步骤\",\"自动化\",\"流程\",\"task\",\"plan\"],\"long_context\":[\"长文本\",\"大文件\",\"全文\",\"全部代码\",\"完整项目\",\"长篇\",\"大量内容\",\"10万行\",\"长上下文\"],\"web_search\":[\"搜索\",\"查一下\",\"查找\",\"资料\",\"文档\",\"最新\",\"官网\",\"信息\",\"查询\",\"search\",\"find\"],\"image\":[\"图片\",\"截图\",\"看图\",\"识别\",\"ocr\",\"视觉\",\"照片\",\"图表\",\"界面\",\"image\",\"screenshot\",\"vision\"],\"compact\":[\"/compact\",\"compact\",\"简洁\",\"简短\",\"精简\",\"简要\",\"概括\",\"一句话\",\"short\",\"brief\"]},\"expert_keywords\":{\"architecture_refactor\":[\"架构\",\"architecture\",\"重构\",\"refactor\",\"优化\",\"设计模式\",\"模块化\",\"拆分\",\"合并\",\"升级\",\"改造\"]}}"
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "帮我看看现在的狼人杀流程是怎么样的。"
                }
            ]
        }
    ],
    "max_tokens": 4060,
    "stream": false
}'
```
返回了
```
{
    "id": "065d0ebb12261329e3387235d8e799f6",
    "type": "message",
    "role": "assistant",
    "model": "MiniMax-M2.7",
    "content": [
        {
            "thinking": "用户问的是关于狼人杀游戏流程的问题，这是一个游戏规则咨询类的请求。\n\n根据关键词库匹配：\n- \"chat_intention\" 包含\"闲聊\"、\"随便问问\"等\n- \"问题分析\" 包含\"问题\"、\"怎么回事\"等\n\n这更像是一个\"闲聊/随便问问\"类型的请求，用户想了解狼人杀的流程规则。\n\n但我需要严格按照要求的JSON格式输出，不输出任何额外文字。",
            "signature": "8fd3d7992c740fa6d9db59ba0b8fe68402a0f7c5952c082ff44a952bedca079f",
            "type": "thinking"
        },
        {
            "text": "{\"workflow_intent\":{\"chat_intention\":[\"闲聊\",\"随便问问\"]},\"task_routing\":{\"cheap_tasks\":[\"搜索\",\"查一下\"]}}",
            "type": "text"
        }
    ],
    "usage": {
        "input_tokens": 605,
        "output_tokens": 117,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0
    },
    "stop_reason": "end_turn",
    "base_resp": {
        "status_code": 0,
        "status_msg": "success"
    }
}
```

## curl 复杂版
```
curl --location 'https://api.minimaxi.com/anthropic/v1/messages' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer xxxx' \
--header 'anthropic-version: 2023-06-01' \
--data-raw '{
    "model": "MiniMax-M2.7",
    "messages": [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "你是请求分流关键词匹配器，**仅输出纯JSON，禁止任何多余文字、解释、备注**。按给定关键词库精准匹配，命中就填入对应数组，无匹配字段直接省略。输出格式严格遵循示例：{\"workflow_intent\":{\"chat_intention\":[\"咋样\"]}}\n关键词库：{\"workflow_intent\":{\"chat_intention\":[\"你好\",\"在吗\",\"嗨\",\"哈喽\",\"谢谢\",\"辛苦了\",\"闲聊\",\"聊聊天\",\"随便问问\",\"早上好\",\"晚上好\",\"hello\",\"hi\",\"hey\",\"thanks\",\"thank you\",\"咋样\",\"还行吗\",\"没事\",\"随便说\"],\"intention_analyze\":[\"我想\",\"我要\",\"帮我\",\"请问\",\"如何\",\"怎么\",\"能不能\",\"可不可以\",\"需要\",\"想要\",\"希望\",\"需求\",\"目的\",\"目标\",\"意图\",\"打算\",\"计划\"],\"problem_analyze\":[\"分析\",\"排查\",\"诊断\",\"原因\",\"为什么\",\"怎么回事\",\"处理\",\"问题\",\"错误\",\"报错\",\"异常\",\"故障\",\"bug\",\"哪里错\",\"为何\",\"根源\",\"定位\",\"梳理\",\"解析\",\"analyze\",\"diagnose\",\"debug\",\"issue\",\"error\",\"你个垃圾\",\"这他妈\",\"白痴\",\"[deep]\"],\"solution_plan\":[\"方案\",\"解决\",\"办法\",\"思路\",\"步骤\",\"规划\",\"设计\",\"策略\",\"建议\",\"指南\",\"教程\",\"怎么做\",\"方案设计\",\"实施方案\",\"解决方法\",\"计划\",\"solution\",\"plan\",\"strategy\",\"guide\",\"how to\"],\"execute_solve\":[\"执行\",\"实现\",\"动手\",\"运行\",\"操作\",\"处理\",\"修改\",\"编写\",\"生成\",\"创建\",\"修复\",\"搞定\",\"落地\",\"做完\",\"部署\",\"启动\",\"调用\",\"执行命令\",\"execute\",\"run\",\"implement\",\"fix\",\"create\"]},\"task_routing\":{\"cheap_tasks\":[\"查看\",\"读取\",\"搜索\",\"列表\",\"状态\",\"日志\",\"cat\",\"ls\",\"grep\",\"git\",\"head\",\"查看文件\",\"读文件\",\"搜内容\",\"查状态\",\"看日志\"],\"standard_tasks\":[\"编辑\",\"写入\",\"修改\",\"新建\",\"保存\",\"更新\",\"写代码\",\"改代码\",\"编辑文件\",\"write\",\"edit\"],\"complex_tasks\":[\"agent\",\"智能体\",\"任务\",\"规划模式\",\"架构\",\"项目\",\"工程\",\"重构\",\"系统\",\"整体\",\"设计\",\"多步骤\",\"自动化\",\"流程\",\"task\",\"plan\"],\"long_context\":[\"长文本\",\"大文件\",\"全文\",\"全部代码\",\"完整项目\",\"长篇\",\"大量内容\",\"10万行\",\"长上下文\"],\"web_search\":[\"搜索\",\"查一下\",\"查找\",\"资料\",\"文档\",\"最新\",\"官网\",\"信息\",\"查询\",\"search\",\"find\"],\"image\":[\"图片\",\"截图\",\"看图\",\"识别\",\"ocr\",\"视觉\",\"照片\",\"图表\",\"界面\",\"image\",\"screenshot\",\"vision\"],\"compact\":[\"/compact\",\"compact\",\"简洁\",\"简短\",\"精简\",\"简要\",\"概括\",\"一句话\",\"short\",\"brief\"]},\"expert_keywords\":{\"architecture_refactor\":[\"架构\",\"architecture\",\"重构\",\"refactor\",\"优化\",\"设计模式\",\"模块化\",\"拆分\",\"合并\",\"升级\",\"改造\"]}}"
                }
            ]
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system-reminder data-role=\"user-context\">\n  ├─ <user_info>                # 用户环境信息\n  │  ├─ OS Version: win32\n  │  ├─ Shell: bash\n  │  ├─ Workspace Folder: 路径\n  │  └─ Note: 路径使用说明\n  │\n  ├─ <project_context>          # 项目核心上下文\n  │  ├─ <project_guidance>    # 项目规范（CDATA包裹）\n  │  │  └─ 项目概述/架构/命令/规则/配置\n  │  └─ <project_layout>       # 项目文件目录结构\n  │\n  ├─ <additional_data>            # 附加数据\n  │  ├─ <current_time>         # 当前时间\n  │  └─ <connector-status>     # 服务连接状态（全disconnected）\n  │\n  └─ <memory_and_skills_reminder>  # 记忆&技能规则\n       └─ 内存写入/技能管理/通用规则\n\n# 重复嵌套结构（交互轮次）\n</system-reminder>\n\n<system-reminder data-role=\"user-context\">\n<user_info>\nOS Version: win32\nShell: bash\nWorkspace Folder: d:\\Users\\viaco\\tools\\Toonflow-game\nNote: Prefer using absolute paths over relative paths as tool call args when possible.\n</user_info>\n<project_context>\n<project_guidance>\n<![CDATA[The project guidance below is generated by the CodeBuddy IDE'\''s AI...\nFile: d:\\Users\\viaco\\tools\\Toonflow-game\\CODEBUDDY.md\n# CODEBUDDY.md This file provides guidance to WorkBuddy when working with code in this repository.\r\n\r\n## Repository Overview\r\n\r\nThis is **Toonflow** - an AI short-form drama and interactive story game platform with two main business lines:\r\n\r\n1. **AI Short Drama Pipeline**: Novel → Outline → Script → Storyboard → AI Image/Video → Final video\r\n2. **Multi-character AI Story Game** (current focus): World creation → Chapter design → Game session → Character interaction → Narrative engine driven\r\n\r\n## Monorepo Structure\r\n\r\n```\r\nToonflow-game/\r\n├── toonflow-game-app/         # Backend API service (Node.js + TypeScript + Express)\r\n├── Toonflow-game-web/         # Frontend Vue3 SPA (Vite + TypeScript)\r\n├── Toonflow-game-android/     # Android client (Kotlin + Compose)\r\n├── Toonflow-vedio-web/        # Video-related web utilities\r\n├── toonflow-app-run-db/       # Runtime media assets (mp3/mp4/images)\r\n└── bak/                       # Backup code\r\n\r\n\r\nEach subdirectory has its own `AGENTS.md` with specific commands and conventions.\r\n\r\n## Backend Architecture (toonflow-game-app)\r\n\r\n### Layered Architecture\r\n\r\n```\r\nsrc/\r\n├── routes/             # Route layer (auto-discovered by core.ts)\r\n│   ├── game/           # AI game APIs (29 endpoints)\r\n│   ├── novel/, outline/, script/, storyboard/, video/, assets/, setting/, user/\r\n│\r\n├── modules/game-runtime/   # Game runtime module (core business)\r\n│   ├── engines/        # Engine layer (pure logic, side-effect free)\r\n│   │   ├── NarrativeOrchestrator.ts   # Narrative orchestration\r\n│   │   ├── ChapterProgressEngine.ts   # Chapter progress state machine (~1400 lines)\r\n│   │   ├── TriggerEngine.ts           # Condition triggers\r\n│   │   ├── RuleOrchestrator.ts        # Rule executor\r\n│   │   └── ChapterOutcomeEngine.ts    # Chapter outcome evaluation\r\n│   ├── services/       # Service layer (with side effects, DB access)\r\n│   │   ├── SessionService.ts          # Session core service\r\n│   │   └── ChapterRuntimeService.ts   # Chapter runtime evaluation\r\n│   └── types/          # Type definitions\r\n│\r\n├── agents/             # AI Agent layer\r\n│   ├── outlineScript/  # Outline/script generation\r\n│   ├── storyboard/     # Storyboard generation\r\n│   └── voicePromptPolish.ts\r\n│\r\n├── lib/                # Shared libraries\r\n│   ├── gameEngine.ts   # Core state structures + state utils (~1900 lines)\r\n│   ├── initDB.ts       # SQLite schema + init data\r\n│   └── voiceGateway.ts # Voice service gateway\r\n│\r\n└── utils/              # Utilities\r\n    └── db.ts           # Knex + SQLite connection\r\n```\r\n\r\n### Key Architectural Patterns\r\n\r\n1. **File System Routing**: `core.ts` auto-discovers routes from `src/routes/**/*.ts` using fast-glob, similar to Next.js but on Express. Routes are auto-registered in `router.ts` (do not edit manually).\r\n\r\n2. **Pure Function State Machine**: Game state uses pure functions (`readXxx`/`setXxx`/`upsertXxx`) from `gameEngine.ts`. State stored as JSON in SQLite.\r\n\r\n3. **Event-Driven Narrative**: Phase → Event → Turn three-layer structure. AI generates content streamingly via Vercel AI SDK.\r\n\r\n4. **Multi-Model AI Adapter**: Supports OpenAI/DeepSeek/Anthropic/Google/xAI/阿里通义/智谱 via `@ai-sdk/*` with unified interface.\r\n\r\n5. **Dual Runtime Modes**:\r\n   - Pure API Server: `yarn dev` on port 60002\r\n   - Electron Desktop: `yarn dev:gui` with embedded Chromium\r\n\r\n## Common Commands\r\n\r\n### Backend (toonflow-game-app/)\r\n```bash\r\n# Development\r\nyarn dev              # Run backend with hot reload\r\nyarn dev:gui          # Run Electron GUI (dev mode)\r\nyarn local:gui        # Run GUI with local env\r\n\r\n# Build & Quality\r\nyarn lint             # TypeScript type-check (tsc --noEmit)\r\nyarn build            # Compile to build/\r\nyarn dist:win         # Package Windows desktop app\r\nyarn test             # Smoke test compiled app\r\n```\r\n\r\n### Frontend (Toonflow-game-web/)\r\n```bash\r\nyarn dev              # Start Vite dev server on :5173\r\nyarn build            # Production build + inline assets\r\nyarn type-check       # Run tsc --noEmit\r\n```\r\n\r\n### Android (Toonflow-game-android/)\r\n```bash\r\n./gradlew assembleDebug      # Build debug APK\r\n./gradlew installDebug       # Install to device/emulator\r\n./gradlew lint               # Android lint checks\r\n./gradlew test               # JVM unit tests\r\n```\r\n\r\n## Critical Implementation Notes\r\n\r\n### State Management Complexity\r\nThe game runtime (`modules/game-runtime/`) uses sophisticated state management:\r\n\r\n- `ChapterProgressState`: Tracks phase ID, event index, user node status\r\n- `RuntimeDynamicEventState`: AI-generated narrative summaries and facts\r\n- `evaluateCondition()`: Supports natural language + JSON expressions (and/or/not/equals/contains/gt)\r\n\r\nWhen modifying game logic, always check:\r\n1. `gameEngine.ts` for state structure changes\r\n2. `ChapterProgressEngine.ts` for phase transition logic\r\n3. `NarrativeOrchestrator.ts` for AI interaction flow\r\n\r\n### Database Schema\r\nSQLite with Knex (no ORM). Key tables:\r\n- `t_game_world`, `t_game_chapter`, `t_game_session`, `t_game_message`, `t_game_snapshot`\r\n- `t_project`, `t_novel`, `t_outline`, `t_script`, `t_assets`\r\n- `t_aiTokenUsageLog` for AI cost tracking\r\n\r\nSchema defined in `initDB.ts`, migrations in `fixDB.ts`.\r\n\r\n### File Modification Rules\r\n- **DO NOT EDIT**: `scripts/web/index.html` (built from Toonflow-game-web)\r\n- **DO NOT EDIT**: `router.ts` (auto-generated by core.ts)\r\n- **DO NOT EDIT**: Files marked with `@no_modify` or `# @no_modify` on first line\r\n- **DO NOT EDIT**: `review_xxx.md` files (user verification files)\r\n\r\n### Cross-Platform Sync\r\nWeb and Android implementations should stay synchronized:\r\n- Consider platform differences (e.g., voice hold-to-talk works on Android but not Web)\r\n- Use Orson font icons instead of text buttons where possible\r\n\r\n### Terminology\r\nUse \"用户\" (user) instead of \"玩家\" (player) throughout the codebase.\r\n\r\n## Development Workflow\r\n\r\n1. **Backend changes**: Edit in `toonflow-game-app/src/`, test with `yarn dev`\r\n2. **Frontend changes**: Edit in `Toonflow-game-web/src/`, test with `yarn dev`\r\n3. **Database changes**: Update `initDB.ts` for new tables, `fixDB.ts` for migrations\r\n4. **API changes**: Add route in `routes/`, auto-discovered on restart\r\n\r\n## System Configuration\r\nEnvironment templates in `env/` (dev/local/prod). System-wide config in `system/system.yml`.\r\n\r\n\r\n## 不允许进行修改的文件说明\r\n- review_xxx.md 文件 \r\n是用户自己验证功能的文件，ai 不允许进行修改\r\nai 可以新增或者修改 review_xxx.answer.md\r\n\r\n## 不允许ai 修改的标注\r\n文件第一行: @no_modify\r\n或者 # @no_modify]]>\n</project_guidance>\n<project_layout>\nBelow is a snapshot of the current workspace'\''s file structure...\nd:\\Users\\viaco\\tools\\Toonflow-game\\\n- 项目分析报告.md\n- android-test.js\n- bak/\n  [13 files in subtree: 6 *.ts, 2 *.json, 1 *.html, 1 *.md, 1 *.vue]\n- CODEBUDDY.md\n- nul\n- orchestrator_user_prompt.txt\n- toonflow-app-run-db/\n  [6001 files in subtree: 3691 *.mp3, 751 *.jpg, 614 *.png, 600 *.mp4, 229 *.wav]\n- Toonflow-game-android/\n  [476 files in subtree: 231 *.png, 59 *.xml, 48 *.ts, 36 *.md, 22 *.json]\n- Toonflow-game-android-h5/\n  [186 files in subtree: 109 *.flat, 14 *.json, 13 *.xml, 12 *.txt, 8 *.png]\n- toonflow-game-app/\n  [27585 files in subtree: 12273 *.js, 5772 *.ts, 2072 *.md, 1958 *.json, 1404 *.map]\n- Toonflow-game-doc/\n  [261 files in subtree: 97 *.png, 69 *.md, 49 *.ts, 7 *.js, 7 *.mjs]\n- Toonflow-game-story/\n  [96 files in subtree: 38 *.md, 30 *.webp, 16 *.png, 10 *.mp4, 2 *.xlsx]\n- Toonflow-game-web/\n  [2008 files in subtree: 561 *.js, 536 *.ts, 234 *.png, 164 *.map, 148 *.md]\n- Toonflow-vedio-web/\n  [79350 files in subtree: 38730 *.js, 24000 *.ts, 8277 *.map, 1680 *.css, 1668 *.svg]\n- web-test-result.png\n</project_layout>\n</project_context>\n<additional_data>\n<current_time>\nWednesday, May 20, 2026 at 23:34:32 GMT+8\n</current_time>\n\n<connector-status>\ncnb-api CNB: disconnected\ncnb-woa CNB（司内版）: disconnected\nedgeone-pages EdgeOne Pages: disconnected\nfbs-connector 福帮手: disconnected\ngithub GitHub: disconnected\ngongfeng-woa Gongfeng: disconnected\niwiki-woa iWiki: disconnected\nkdocs 金山文档: disconnected\nlexiang 乐享知识库: disconnected\nnotion Notion: disconnected\nqcc-company 企查查: disconnected\nqq-mail QQ邮箱: disconnected\ntapd TAPD: disconnected\ntapd-woa TAPD（司内版）: disconnected\ntencent-docs 腾讯文档: disconnected\ntencent-survey 腾讯问卷: disconnected\ntencent-weiyun 微云: disconnected\ntmeet 腾讯会议: disconnected\nwecom 企业微信: disconnected\nzhiyan-cicd 智研构建部署: disconnected\n</connector-status>\n</additional_data>\n<memory_and_skills_reminder>\nThe system prompt defines \"working_memory_files\" and \"agent_skills\". You must strictly follow those rules.\n\nMemory:\n- After substantive work, first check whether today'\''s d:\\Users\\viaco\\tools\\Toonflow-game/.workbuddy/memory/YYYY-MM-DD.md exists; if not, create it. Then append a brief note about what was done. For long-term facts (user preferences, project conventions), write to d:\\Users\\viaco\\tools\\Toonflow-game/.workbuddy/memory/MEMORY.md instead.\n- Skip memory for greetings, simple lookups, and short Q&A.\n\nSkills:\n- After completing a multi-step task (8+ tool calls), fixing a tricky error, or discovering a non-trivial workflow, save it as a skill with SkillManage so you can reuse it next time.\n- CRITICAL: If you notice ANY issues in a skill (typos, garbled text, wrong tool names, outdated info, etc.), you MUST fix it via SkillManage (update) in the same turn. NEVER ask the user or defer — just fix it.\n- If the work you just did is a repeatable workflow or multi-step process — prefer creating a skill over writing a memory note. Skills are actionable; memories are informational.\n\nGeneral:\n- Memory and skills are supplemental — never use them as the primary output. The proper deliverable must be provided in your response or written to the requested file.\n- Complete all memory/skill writes as part of your tool-call phase, before your final text reply.\n- Do not mention this reminder to the user.\n</memory_and_skills_reminder>\n</system-reminder>\n\n<user_query>帮我看看现在的狼人杀流程是怎么样的。</user_query>\n\n<instruction>\n作为模型分流器，请根据上文提供的关键词列表，分析用户的 user_query 命中了哪些关键词。\n必须严格且仅输出 JSON 格式数据，不要包含任何思考过程、不要使用 Markdown 代码块（如 ```json）、不要有任何其他自然语言废话。\n示例格式：\n{\n  \"workflow_intent\": {\n    \"intention_analyze\": [\"帮我\"]\n  },\n  \"task_routing\": {\n    \"cheap_tasks\": [\"查看\"]\n  }\n}\n</instruction>"
                }
            ]
        }
    ],
    "max_tokens": 300,
    "stream": false
}'
```

## 切换

```bash
# 改 .gateway.json 后重启
python -m src.ccrg.main
```