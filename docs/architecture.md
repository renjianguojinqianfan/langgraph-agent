# 系统架构：基于 LangGraph 的自主任务 Agent 平台

> **状态：现役架构文档**（2026-10-23 全量刷新）。旧的 v0.1 原始设计档（2025-08-24）原文保留在 git 历史：`git log --follow docs/architecture.md`。
>
> 本文讲「是什么、怎么连」；**决策理由**在 [`docs/adr/`](adr/)（现役：0001、0002（部分退役）、0008、0009、0010），**能力清单**在 [`README.md`](../README.md)「能力」，**跨任务硬规则**在 [`AGENTS.md`](../AGENTS.md)「跨任务安全边界」，**发布前验收**在 [`docs/manual-verification.md`](manual-verification.md)，**安全边界与暴露面**在 [`docs/security.md`](security.md)。
>
> **契约面有三处机械守卫，改之前先读**：
> - **§3.4 事件表**被 `npm run check:sse`（`frontend/scripts/check-sse-event-registry.mjs`）与 `backend/tests/test_sse_publish_ast.py` 双向解析；`docs/architecture.md` 是 CI `paths` 白名单的**唯一 docs 例外**——改它触发全套 CI（见 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)）。
> - **§3.3 REST** 以 [`backend/api/routes.py`](../backend/api/routes.py) + 运行时 Swagger 为准；本节是人的可读契约。
> - **拓扑**以 [`backend/core/agent/graph.py`](../backend/core/agent/graph.py) 为准；**数据模型**以 [`backend/api/schemas.py`](../backend/api/schemas.py) 为准；**配置键**以 [`backend/config.py`](../backend/config.py) + [`.env.example`](../.env.example) 为准。
>
> 证据锚：`master` = `9c94a90`（2026-10-22）。文中 `file:line` 以该 commit 为准。

---

## 1. 定位与技术栈

自然语言任务 → **一条 loop、计划 / 执行两模式** → 工具调用 → 停下时人看工作区里的文件。重点在运行时（多轮续说、checkpoint、模式切换、危险动作拦截、工具生态、可观测性），不是一个 demo 界面。产品形态由 ADR 0010 补充：**启动目录即工作区、一台机器共用一个后台、多套模型配置**。

| 层 | 选型 | 说明 |
|---|---|---|
| 编排 | **LangGraph 1.2.x（StateGraph）** | 唯一一张图：`agent` / `tool` / `finish`；checkpointer 落 sqlite；1.x 迁移实测见 [`migration-langgraph-1x.md`](migration-langgraph-1x.md) |
| 后端 | Python 3.11 · FastAPI · pydantic-settings | 离线测试零网络零 Key（MockLLM）；类型/质量门禁见 `pyproject.toml` |
| 前端 | React 18 · Vite 8 · TypeScript · Tailwind 4 · zustand | 单页应用，`frontend/dist` 由后端同源托管（`qianfan` 命令） |
| 存储 | sqlite（checkpoint）+ JSON（任务/模型档案）+ JSONL（trace） | `data/` 为运行态、不入库；Key 在仓库外（`~/.qianfan/credentials.json` 或环境变量） |
| 通信 | REST + **SSE**（单向事件流） | SSE 事件词汇表是本仓最强的机械契约（§3.4） |

**运行形态**：本机单用户、单进程。默认只绑定回环地址；日常入口是 `qianfan` 控制台命令（`pyproject.toml` 的 `[project.scripts]`，实现在 [`backend/cli.py`](../backend/cli.py)），开发入口是 `python start.py`（只用于改前端时热重载），两者互斥（`backend/server_lock.py`）。

---

## 2. 目录结构与运行时组件

```text
backend/
├── config.py            Settings（pydantic-settings；.env / 环境变量）
├── main.py              FastAPI app：/health、/api 路由挂载、静态前端（StaticFiles）、Host 检查、CORS
├── cli.py               qianfan 入口：单后台、自动开浏览器、前端构建新鲜度检查
├── server_lock.py       单实例锁（一台机器只允许一个后台）
├── api/
│   ├── routes.py        27 条 REST 路由（§3.3）；统一 {code,data,message} 信封
│   ├── schemas.py       Pydantic 模型：API 与持久化的单一来源（§3.1）
│   ├── sse.py           SSE 流组装（15s heartbeat）
├── core/
│   ├── agent/           graph.py 拓扑 · nodes.py 三节点 · state.py AgentState
│   │                    prompts.py 两套 system · context.py 压缩/驱逐 · inject.py 两层注入
│   │                    policy.py 危险动作钩子 · subagent.py 子任务运行时
│   ├── llm/             client.py（LLMClient 抽象 + MockLLMClient）· openai_compat.py
│   ├── tools/           base.py 契约 · registry.py @register · file_io.py（read/write/edit/ls/glob/grep）
│   │                    bash.py · web_search.py · skill_tool.py · subagent_tool.py · mcp_tool.py
│   ├── mcp/             MCP 客户端（stdio 现役；http 预留）
│   └── workspace_files.py  工作区文件可见性过滤（W3）
├── services/            task_manager.py 生命周期/worker/续说/续跑/事件 · event_bus.py
│                        persistence.py tasks.json · plan_file.py 计划文件 · trace.py JSONL trace
│                        model_store.py 模型档案与凭据 · auth.py token 签发/校验
└── utils/               logging.py · sandbox.py（bash 进程组/超时）· winjob.py（Windows Job Object）

frontend/src/
├── pages/               TaskView · LoginPage
├── components/          TaskPanel · AuthGuard · TraceTab
│   ├── center/          TaskHeader · Composer · Conversation · TurnProcess · UserBubble
│   │                    DisclosureHead · CompressMarker · StepRow · ToolRow · AnswerSheet
│   │                    FailureBlock · PlanAnchor · SubagentStrip · SubagentPlate
│   ├── rail/            TaskRail · RailSearch · TaskRow · RailFooter · group.ts
│   ├── dash/            DashTabs · FilePane · PlanPane · EventPane · TraceFace
│   ├── settings/        SettingsDialog · ModelRow · FormField
│   └── ui/              Button · Icon · Status · Segmented · Tabs
├── store/               taskStore · reduceTask · modelStore · authStore
├── hooks/               useSSE.ts EventSource 订阅表 EVENT_TYPES（§3.4 的四处之一）· useMediaQuery.ts 断点
├── lib/                 format · taskState · rounds · markers · markdown · keys · cx · eventLabels.ts
├── api/client.ts        后端调用
└── types/index.ts       与 schemas.py 字段对齐；SSEventType 联合

docs/                    adr/ · specs/（冻结归档）· agents/（agent 规则）· archive/（历史）· 本文件
scripts/                 check_doc_links.py 等
data/（运行态，不入库）   tasks.json · models.json · traces/<task_id>.jsonl · checkpoints/checkpoints.sqlite · logs/
```

| 运行时组件 | 职责 | 权威位置 |
|---|---|---|
| `TaskManager` | 任务生命周期：创建 / worker 执行 / 停止 / 续说 / 续跑 / 崩溃对账 / 事件发布；权威停止信号 `_stop_flags` | [`backend/services/task_manager.py`](../backend/services/task_manager.py) |
| `EventBus` | 进程内 SSE 事件广播；前端按 task 订阅 | [`backend/services/event_bus.py`](../backend/services/event_bus.py) |
| `Persistence` | `tasks.json` 原子落盘 + 损坏备份；API 与磁盘的唯一桥 | [`backend/services/persistence.py`](../backend/services/persistence.py) |
| checkpointer | `SqliteSaver`，`thread_id == task_id`；`durability="sync"`；格式戳 v2，v1 旧库启动时拒绝挂载 | [`backend/services/task_manager.py`](../backend/services/task_manager.py) |
| `plan_file` | 计划文件唯一来源在任务目录 `.qianfan/plans/<task_id>.md`；版本与 hash | [`backend/services/plan_file.py`](../backend/services/plan_file.py) |
| `TraceRecorder` | 订阅事件直写 JSONL（含唯一只进文件的 `trace_end`）；前端回放/导出 | [`backend/services/trace.py`](../backend/services/trace.py) |
| `ModelStore` | `data/models.json` 档案（不含 Key）+ `~/.qianfan/credentials.json`（粘贴的 Key） | [`backend/services/model_store.py`](../backend/services/model_store.py) |
| `DangerPolicy` | 设置里的 `policy_rules` 规则表 + `agent` 里一个钩子；命中当场拒 | [`backend/core/agent/policy.py`](../backend/core/agent/policy.py) |

---

## 3. 数据结构与接口契约

### 3.1 数据模型（API / 持久化的单一来源）

全部定义在 [`backend/api/schemas.py`](../backend/api/schemas.py)；前端 [`frontend/src/types/index.ts`](../frontend/src/types/index.ts) 字段同名对齐。

| 模型 | 字段要点 |
|---|---|
| `TaskStatus` | **四值**：`PENDING`（创建到 worker 抢到之间的瞬间）/ `RUNNING` / `INTERRUPTED`（停下，等你说话）/ `FAILED`。**没有 `COMPLETED`**（ADR 0008 决策 5）；旧记录里的 `COMPLETED` 在加载时映射成 `INTERRUPTED` |
| `TaskMode` | `plan` \| `build`（黏性任务开关，默认 `build`） |
| `ToolCallRecord` | `id` / `tool_name` / `input` / `output` / `status`（`pending \| success \| failed \| skipped`）/ `error` |
| `StepRecord` | `index` / `round`（1 起的对话轮次，`0` = #265 前的旧记录）/ `thought` / `tool_calls[]` / `status`（`pending \| running \| done \| failed`） |
| `SubTask` | `subtask_id` / `name` / `status`（`pending \| running \| completed \| failed`）/ `summary` / `artifacts[]` / `error`。`artifacts` 是**现役字段**（子任务回传用，父任务无产物账本） |
| `ChatTurn` | `role`（user \| assistant）/ `content`；只存人读的对话，工具流量在 `Task.steps` |
| `Task` | `id` / `title` / `user_input` / `workspace`（绝对路径，规范化后比较）/ `model`（档案 id，空 = 全局默认）/ `status` / `mode` / `steps[]` / `plan_version` / `plan_hash` / `conversation[]` / `final_answer` / `error` / `created_at` / `updated_at` / `subtasks[]` |
| `McpServerInfo` | `name` / `transport` / `status` / `tools_count` / `error` |
| `ApiResponse` | 统一信封 `{code, data, message}`（例外见 §3.5） |

请求体模型同文件：`CreateTaskRequest`（`title?` / `input` / `mode?` / `workspace?` / `model?`）、`SendMessageRequest`、`UpdateModeRequest`、`UpdatePlanRequest`、`TaskModelRequest`、`ModelCreate/Update/KeyRequest`、`AuthTokenRequest`。

### 3.2 AgentState 与图拓扑

**拓扑**（来源：`backend/core/agent/graph.py`；主任务与子任务同一张图）：

```text
START → agent ──有 tool call──→ tool ──→ agent
          └── 停下/出错/本轮最后一次调用没有 tool call ──→ finish → END
```

- 只有**一个 LLM 节点** `agent`；模式不是第二个节点，只决定该节点用哪套 system prompt 与哪个工具面（ADR 0008 决策 2）。
- 路由是带 `Literal` 注解的具名函数（LangGraph 1.x 用注解声明静态拓扑）：`_after_agent` 走 `finish | tool`；`_after_tool` 走 `finish | agent`，并在 `round_steps >= max_steps` 时收口。
- **步数预算按轮计**：`round_steps` 只数本轮 `agent` 调用，不封顶任务总寿命（`max_steps` 默认 15）。
- 子任务复用同一拓扑，在独立 `thread_id` 上跑，工作区继承父任务。

**AgentState**（`backend/core/agent/state.py`，TypedDict）分两层：公开/持久化字段（`task_id` / `mode` / `messages` / `plan_text` / `plan_version` / `plan_hash` / `steps` / `status` / `stop_requested`）与循环簿记（`round_no` / `step_index` / `round_steps` / `final_answer` / `error` / `compressed` / `context_tokens` / `_last_action` / `_current_tool_calls`）以及子任务字段（`subtasks` / `_is_subtask`）。

**conversation 账本**：`Task.conversation` 是逐轮只增的用户 / 助手文本账本。收尾（`_finalize_terminal` → `_merge_round_turns`）只追加本轮新增的两样东西——该轮的计划提示与末次回答——不拿 `final["messages"]` 重建：压缩可能已丢掉早期回合，而消息面只保留最新一条计划提示。旧计划提示留在原位（它就是那一轮的回答）；轮次编号按账本里的用户回合数计，压缩因此不会让编号回退。

**checkpoint 语义**：挂载后每个 superstep 都是一份 state 副本并持久化；`thread_id == task_id`，因此停止/崩溃可续跑，停下后也能沿同一 thread 续说。`stop_requested` 在带 checkpointer 时不可靠（每步是副本），所以 `agent` 节点额外轮询 manager 级的 `_stop_flags`（`task_manager.py`）。

### 3.3 REST 接口定义

**基础路径**：`/api`（`backend/main.py` 用 `include_router(..., prefix="/api")` 挂载）；`/health` 不在前缀下。**内容类型**：`application/json`（例外：`/events` 为 `text/event-stream`，`/trace` 默认 `application/x-ndjson`，`/files/download` 为文件流）。

所有端点除 `POST /api/auth/token` 外都挂 `Depends(verify_token)`；`auth_enabled=false` 时它是 no-op。

| 方法 | 路径 | 说明 | 请求体 / 参数 | 响应 `data` |
|---|---|---|---|---|
| GET | `/health` | 健康检查（无 `/api` 前缀） | — | 服务状态 |
| POST | `/api/tasks` | 下发新任务（默认 build 模式） | `{title?, input, mode?, workspace?, model?}` | `{task_id}`；空 `input` → 400 / 落盘失败 → 500（`{detail}`） |
| GET | `/api/tasks` | 历史任务列表 | `?limit=50&dir=current\|<path>\|all` | `{tasks: Task[]}` |
| GET | `/api/workspaces/validate` | 校验并归一化一个工作区目录（#242；只读，不列目录） | `?path=<路径>` | `{path}`；空 / 不存在 / 非目录 / 非本地路径 → 400 |
| DELETE | `/api/tasks` | 删除某目录下全部任务历史（#241；`dir` 必填，不提供「全部」） | `?dir=<路径>` | `{deleted}`；400 / 任一运行中 → 409（不删）/ 落盘失败 → 500（整批回滚） |
| DELETE | `/api/tasks/{id}` | 删除任务历史：记录 + checkpoint + trace（不改磁盘文件；任务名下后台进程一并终止，#241） | — | `{ok, cleanup:{checkpoint,trace}}`；404 / 运行中 → 409 / 落盘失败 → 500（不清理） |
| GET | `/api/tasks/{id}` | 任务完整状态 | — | `Task`；不存在 → 404 |
| POST | `/api/tasks/{id}/stop` | 停止任务 | — | `{ok, status}`；落盘失败 → 500（`{detail}`） |
| POST | `/api/tasks/{id}/resume` | 从最近 checkpoint 续跑（无新消息路径） | — | `TaskManager.resume` 结果；不能续 → 409 / 落盘失败 → 500（`{detail}`） |
| PUT | `/api/tasks/{id}/model` | 换任务模型（下一条消息生效） | `{model}` | `TaskManager.set_model` 结果；不能改 → 409 / 落盘失败 → 500（`{detail}`） |
| PUT | `/api/tasks/{id}/mode` | 切计划/执行模式（黏性，下一轮生效） | `{mode}` | `TaskManager.set_mode` 结果；不能切 → 409 / 落盘失败 → 500（`{detail}`） |
| POST | `/api/tasks/{id}/messages` | 停下 / 失败后续说（同一 thread，多轮对话入口；需 checkpoint） | `{input}` | `TaskManager.send_message` 结果；不能续 → 409 / 落盘失败 → 500（`{detail}`） |
| GET | `/api/tasks/{id}/plan` | 计划文件原文与版本 | — | `{text, version}`；读不到 → 404/409 |
| PUT | `/api/tasks/{id}/plan` | 写回计划文件（仅停下 / 失败时） | `{text}` | `{ok, version}`；其他状态 → 409 / 落盘失败 → 500（`{detail}`） |
| GET | `/api/tasks/{id}/events` | **SSE 流式事件**（§3.4） | — | `text/event-stream` |
| GET | `/api/tasks/{id}/trace` | 持久化 trace | `?format=ndjson\|json` | 默认原始 NDJSON；`?format=json` 走信封 `{data:[Event...]}`；无 trace → 404 |
| GET | `/api/tasks/{id}/files` | 工作区文件列表（可见性过滤，最多 2000 条） | — | `{files:[{path,size}], truncated}`；工作区不存在 → 409 |
| GET | `/api/tasks/{id}/files/preview` | 预览工作区文本文件（上限 200 KB） | `?path=<相对路径>` | `{path,content,size,truncated}`；二进制 → 415；越界 → 400 |
| GET | `/api/tasks/{id}/files/download` | 流式下载工作区文件 | `?path=<相对路径>` | 文件流（media_type 按扩展名推断，回退 `application/octet-stream`） |
| GET | `/api/models` | 模型档案列表（不含 Key） | — | `[public profile...]` |
| POST | `/api/models` | 新建模型档案 | `{name, base_url, model, api_key_env?}` | public profile；非法 → 400 |
| PUT | `/api/models/{id}` | 更新模型档案 | `{name?, base_url?, model?, api_key_env?}` | public profile；404/400 |
| DELETE | `/api/models/{id}` | 删除模型档案 | — | `{ok}`；404/409 |
| POST | `/api/models/{id}/default` | 设为全局默认 | — | `{ok, default}`；404 |
| PUT | `/api/models/{id}/key` | 写入粘贴的 Key（只写） | `{key}` | `{ok, key_set, key_source}`；404 |
| POST | `/api/models/{id}/test` | 测试模型连通性 | — | 测试结果；404 |
| POST | `/api/auth/token` | 登录签发（`auth_enabled` 关闭时回 `{ok, note}`） | `{token}` | 关闭：`{ok, note}`；开启：`{token, expires_at, ok}`；错误口令 → 401 |
| GET | `/api/mcp/servers` | MCP 服务器连接状态 | — | `{servers: McpServerInfo[]}`（未启用 → `[]`） |

> 契约细节（状态码、字段形状）以 [`backend/api/routes.py`](../backend/api/routes.py) 与运行时 Swagger `/docs` 为准；本表与路由的「方法+路径」由人工维护，无自动守卫——新增/删除端点时**必须**同步本表。

### 3.4 SSE 事件协议（步骤事件 schema）

> ⚠️ **本表是现役事件清单的唯一权威，不写计数。**
> v0.1 时只有 12 种；此后新增/退役都在下表行内标了来源。
> **改本表的 PR 会触发全套 CI**（`ci.yml` 的 `paths` 白名单用末尾正模式把本文件单独收回）。
> **守卫链**：`npm run check:sse`（前端 job）逐条比对「后端发布点 == 本表 == `EVENT_TYPES`」，并校验 `SSEventType` 联合 = `EVENT_TYPES ∪ trace 文件独有类型`、事件页中文标签表（`frontend/src/lib/eventLabels.ts`）的键集 = `EVENT_TYPES`；`backend/tests/test_sse_publish_ast.py`（backend job）用 AST 枚举发布调用，与上表第一列比对。两条路径都只认**字符串字面量**类型：经变量名传入的发布调用是共同盲区，必须手工同步四处。
> **唯一不对称**：`trace_end` 只写 trace JSONL、不经 EventBus——进 `SSEventType` 联合、不进 `EVENT_TYPES` 订阅表。
> 子智能体快照仍以 REST 的 `Task.subtasks` 为准；F7 起前端 reducer 同时消费父频道的 `subtask_*` 三条事件做实时态（`SubagentStrip`），两者只影响子智能体条，不进对话/步骤语义。`subtask_*` 三条只发在**父频道**，子任务自己的频道不带。
> **投递语义**：单个发布者内的事件按发布顺序推送；跨发布者（worker 线程与 `stop()` 的 HTTP 线程）不保证顺序，但回放与实时之间**不丢不重**。晚订阅者先收到缓冲区回放，再继续实时流——`EventBus.subscribe` 在同一把锁里登记订阅并取回放快照，`backend/api/sse.py` 把 worker 线程的发布经 `call_soon_threadsafe` 投回事件循环。

`GET /api/tasks/{id}/events` 推送 `event: <type>\ndata: <json>\n\n`：

| event 类型 | data 字段 | 含义 |
|------------|-----------|------|
| `task_created` | `{task_id, title, status}` | 任务已创建 |
| `step_start` | `{index, round, thought, model, model_profile}` | 一次 `agent` 调用（步）开始；一轮可含多步（工具往返），两种模式同形。`round` 是该步所属的 1 起对话轮次（#265，与 `StepRecord.round` 同源）；`model` / `model_profile` 是本次调用用的模型名与配置 id（M1 / ADR 0010） |
| `tool_call` | `ToolCallRecord` | 即将/正在调用工具 |
| `tool_result` | `ToolCallRecord`（含 `output/status`） | 工具返回 |
| `final_answer` | `{answer:str}` | 本轮的文字回复（不是终态；下一轮会覆盖） |
| `task_failed` | `{task_id, error}` | 失败 |
| `task_interrupted` | `{task_id, status}` | 停下：人按停、这一轮的最后一次 `agent` 调用没有工具调用、或步数用尽——界面上不加区分（ADR 0008 决策 5） |
| `task_resumed` | `{task_id, status}` | 从检查点续跑 / 新消息续跑启动（P3；#139 起多轮对话共用） |
| `tool_result_evicted` | `{tool_call_id, tool_name, original_chars, step_index}` | 旧的大段 tool 结果被换成占位（T1.4，全文留 trace） |
| `context_compressed` | `{step_index, dropped, band_evicted, converged, context_tokens, strategy, model, model_profile}` | 上下文压缩发生。#49 起含带内挤压：`band_evicted` = 保护带里被挤成便签的条数；`converged` = 结果是否已进预算，`false` = 角色闸门下够不到、已如实停手不空转；`strategy=summarize` 时同样可非 0，`dropped=0` 时也可非 0（#85：摘要与截断共享带内挤压） |
| `subtask_start` | `{subtask_id, name, status, parent_task_id}` | 子任务已派生（#70 裁定 A 恢复；只发在父频道） |
| `subtask_result` | `{subtask_id, name, status, summary, artifacts}` | 子任务 `completed` 的折叠回传（#70 裁定 A；父频道）。`artifacts` 是现役字段 |
| `subtask_failed` | `{subtask_id, name, error}` | 子任务超时 / worker 崩溃 / 折回 `failed`（#70 裁定 A；父频道，与一条失败的 `tool_result` 并存、父任务不死） |
| `heartbeat` | `{}` | 保活（每 15s） |

### 3.5 错误口径与响应信封

- **统一信封**：除 `/events`（SSE）、`/trace` 默认 NDJSON、`/files/download`（文件流）三条路径外，成功响应都是 `{code:int, data:any, message:str}`；HTTP 层错误用 FastAPI 的 `detail`（400/401/404/409/415/500）。
- **工具失败两个面各说一次**（#95）：事件面 `tool_result.error`（人可见），对话面 `role:"tool"` 消息 `{"ok": false, "error": ...}`（模型可见）——两者取同一个 `rec["error"]`。
- **被规则当场拒的调用**走第三形状（ADR 0008 决策 6）：对话面 `{"block": true, "reason": ...}`，记录面 `status:"skipped"` + 理由在 `error`（「不许做」与「做失败了」分得开）。
- **只执行一次**：工具调用无自动重试、无熔断（ADR 0003 已退役）；失败原因原样回给模型。
- **任务级异常**：节点级异常被 `TaskManager` 捕获并发布 `task_failed`（`status=FAILED`）。

---

## 4. 运行时流程

### 4.1 一轮

1. `TaskManager` 为任务创建 `AgentRuntime`（LLM client + 工具实例 + 工具 schema）并 `invoke` 图，`thread_id = task_id`。
2. `agent` 节点：取当前模式 → 组装 system（两套 prompt 之一 + 注入块）与对话 → 读计划文件（build 模式、轮开始处）→ 调模型 → 记录一步与 `step_index/round_steps` → 有 tool call 则路由到 `tool`，否则本轮结束。
3. `tool` 节点：逐条执行工具（危险规则命中则当场拒），回填 `ToolCallRecord` 与对话消息，然后按预算/停止信号路由回 `agent` 或 `finish`。
4. `finish`：发 `final_answer`、写对话与计划状态，任务进入 `INTERRUPTED`（停下）。

**「一轮」= 一次 `invoke`（START→END，一条续说）；「步」= 一轮内的一次 `agent` 调用**（`round_steps` 按步计预算）。用户每发一句话开一轮，`StepRecord.round` 记这一步属于哪一轮（1 起，0 = #265 前的旧记录）。一轮的最后一次 `agent` 调用没有工具调用，这一轮就结束（ADR 0008 决策 4）。

### 4.2 停下 / 续说 / 续跑

| 路径 | 触发 | 机制 |
|---|---|---|
| 停止 | 用户按停 | `TaskManager.stop()` 翻 `_stop_flags` 与 state、发布 `task_interrupted`；worker 在节点入口检测后收口，状态 `INTERRUPTED` |
| 续说 | 停下后发消息 | `POST /api/tasks/{id}/messages`：同一 `thread_id` / checkpoint 继续同一段对话（多轮入口） |
| 续跑 | 停止 / 崩溃后 | `POST /api/tasks/{id}/resume`：从最近 durable checkpoint 重建并继续；发布 `task_resumed` |
| 崩溃对账 | 进程重启 | 启动时把 `tasks.json` 里非终态记录翻成 `INTERRUPTED`（可恢复），清扫悬空 tool call |

checkpoint 采用 `SqliteSaver` + `durability="sync"`（`task_manager.py:58-69`），格式戳 v2；v1 旧库启动时拒绝挂载并按迁移文档 §2.4 处置。`data/` 可弃，不做跨大版本快照兼容。

### 4.3 模式与计划文件

- 模式是**任务上的黏性开关**，默认 `build`，用户显式切才走 `plan`（ADR 0008 决策 3）；切换在下一轮生效。
- `plan` 模式：`PLANNER_SYSTEM` + `explore` 只读档；一轮没有工具调用时把回复里的计划写进计划文件。
- `build` 模式：`EXECUTOR_SYSTEM` + 全部已挂载工具；计划以**一条对话消息**进入上下文，不是 system 块。
- **计划文件是唯一来源**：`<任务目录>/.qianfan/plans/<task_id>.md`；版本与内容 hash 记在 `Task.plan_version` / `plan_hash`。计划只在**一轮开始**与**每次压缩后**核对；版本变动时在对话里留**恰好一条**灰字 `计划已更新到第 N 版。`。
- 停下时可整份编辑：页面的计划区走 `PUT /api/tasks/{id}/plan` 写回文件，外部编辑器改同一文件同样生效；下一条消息按文件当前内容跑。
- `.qianfan/.gitignore` 内容为 `*`，工作区文件列表会跳过它，不污染用户的 `git status`。

### 4.4 上下文供给链

1. **注入块**（`context_inject_enabled`，默认开）：home 层 `~/.agents/AGENTS.md` → 任务目录 `AGENTS.md` 两层合并 + skills 清单（字符预算，超额从尾部整卡丢弃）+ 环境事实；每任务计算一次，**不计入压缩预算**，永不淘汰。实现 [`backend/core/agent/inject.py`](../backend/core/agent/inject.py)。
2. **工具结果驱逐**（`context_evict_enabled`，默认开）：单条 tool 消息超过阈值（默认 4000 字符）时替换为「标记 + 工具名 + 原文字数 + trace 指针 + 首尾预览」；最近 N 条永不驱逐；纯机械、零额外 LLM 调用。发 `tool_result_evicted`。
3. **压缩**（`context_*`）：估算 token（chars/4）超预算或条数超限时触发；默认 `truncate`（零额外调用），可选 `summarize`（用当前模型写摘要）。先驱逐、后压缩（顺序固定）。发 `context_compressed`。
4. 计划的版本核对插在第 1、3 步之间/之后（见 §4.3）。

### 4.5 工具执行与危险动作

- 工具注册：`BaseTool` 子类 + `@register`，由 `registry.build_tools` 自动实例化（新增工具零接线）。
- **MCP 工具例外**：由 `task_manager` 显式追加（不经 `@register`）；每次 `call_tool` 只执行一次。
- 危险动作：`policy_rules`（JSON 数组，`{tool, pattern?, reason?}`）在 `agent` 节点里由**一个钩子**匹配入参；命中即不执行，把 `{block, reason}` 当该次调用结果回给模型，记录 `status="skipped"`。**不弹框、不进图重入、不改参数、不 terminate**（与 pi 的有意分歧，ADR 0008 决策 6）。空表 = 不拦，它不是安全边界（见 §7）。
- 失败回传：见 §3.5；无自动重试 / 熔断。

### 4.6 子任务

- 单入口工具 `spawn_subagent`（`subagent_enabled`，默认开）；两档工具面 `explore`（只读）/ `execute`，只能比父任务**收窄**。
- 子任务在独立 `thread_id` 上跑同一张图，工作区继承父任务；父频道只收 `subtask_start / subtask_result / subtask_failed`，子任务自己的频道不带这三条。
- `SubTask` 记录折进父任务 `Task.subtasks`；超时（`subagent_timeout_sec`）或崩溃折回 `failed`，与一条失败的 `tool_result` 并存，父任务不死。
- 并发由 `subagent_max_concurrency` 控制（1 = 串行）。

### 4.7 事件与可观测

- `EventBus` → SSE → 前端 `taskStore.applyEvent` 进时间线；EventSource 只派发已注册类型（§3.4 守卫的原因）。
- `TraceRecorder` 订阅同一事件流写 `data/traces/<task_id>.jsonl`，含只进文件的 `trace_end`；前端可回放 / 导出，`GET /trace` 供外部读取。
- 停止/续跑/压缩/驱逐/子任务都是同一条事件流上的可观测对象。

### 4.8 工作区与文件接口

- 任务拥有一个目录：`Task.workspace`（绝对路径；创建时规范化 `~` / 相对路径 / `..` / 符号链接，比较用 `workspace_key`）。日常入口 `qianfan` 的启动目录就是新任务的工作区（ADR 0010 决策 1/2）。
- 文件工具与工作区文件接口的路径**限定在任务目录**；`bash` 不受围栏——工作目录只是起点（见 §7）。
- 文件列表按 `.gitignore` 类规则做**可见性过滤**，不是安全边界：点名 `preview` / `download` 仍可达工作区内的任意文件。
- `artifacts_path`（`data/artifacts`）只作**无任务时的 fallback**（注入块发现、子任务无父工作区等场景），不是现役产物账本。

---

## 5. 跨文件共享约定

> 与 [`AGENTS.md`](../AGENTS.md)「跨任务安全边界」同源；冲突时以 AGENTS.md 的硬规则为准。

1. **配置管理**：所有配置经 [`backend/config.py`](../backend/config.py) 的 `Settings`（pydantic-settings）从 `.env` / 环境变量读取；业务代码用 `get_settings()` 单例，**禁止**硬编码 Key/路径。例外：`ModelStore` 自带 `data/models.json` 档案与仓库外 `~/.qianfan/credentials.json`（Key 只在那里）。
2. **LLM 接入**：业务只依赖 `LLMClient` 抽象；`LLMResponse{content, tool_calls, raw, reasoning_content}`。现役实现 = `OpenAICompatibleClient`（唯一真实供应商路径，OpenAI 兼容）+ `MockLLMClient`（离线脚本）。**选哪套模型不再是硬编码分支**：由 `ModelStore` 档案（`data/models.json`）+ 任务 `Task.model` / 全局默认决定；预设只有 DeepSeek，其余用自定义 `base_url`。
3. **工具接口**：`name / description / args_schema(JSON Schema) / requires_confirm / registers_artifact / run(**kwargs) -> ToolResult`。`run` 不得抛未捕获异常（内核兜底）；新工具 `@register` 即被自动发现。**现状**：`requires_confirm` 是确认闸门退役后的**兼容字段，已无消费者**；`registers_artifact` 只剩子任务产物收集在用；MCP 工具不经 `@register`，由 `task_manager` 追加。
4. **事件/消息协议**：后端 → 前端只经 SSE（§3.4）；`data` 字段名与后端模型一致；`frontend/src/types/index.ts` 与 `schemas.py` 字段对齐（单一来源 `schemas.py`）。字段级无机械守卫，只有事件**类型名**有。
5. **错误口径**：见 §3.5（失败两个面 + 规则拒绝第三形状 + 只执行一次）。
6. **停止信号**：权威信号是 `TaskManager` 级的 `_stop_flags`（带 checkpointer 时 state 是每 superstep 副本，不可靠）；`stop()` 同时翻 state 与 flag，**`task_interrupted` 由 `TaskManager` 发布**（节点从不发布它）。
7. **危险动作拦截**：设置里的 `policy_rules` 规则表 + `agent` 里一个钩子；命中当场拒、不弹框、不进图重入（ADR 0008 决策 6）。
8. **路径安全**：`read/write/edit/ls/glob/grep` 与工作区文件接口限定在当前任务目录；`bash` 不受此围栏；文件列表是可见性过滤，不是安全边界（`bash` 拿运行账号权限）。
9. **停下判定**：一轮的最后一次 `agent` 调用没有工具调用 → `finish` 停成 `INTERRUPTED`；错误 → `FAILED`；**没有 `COMPLETED` 终态**。
10. **日志**：统一经 [`backend/utils/logging.py`](../backend/utils/logging.py)，结构化输出到控制台 + `data_dir/logs/`；不打印明文 Key。

---

## 6. 依赖与运行形态

### 6.1 依赖

| 面 | 现役钉版（详见源文件） |
|---|---|
| 存储/编排三包 | `langgraph>=1.2,<1.3` · `langgraph-checkpoint==4.3.0` · `langgraph-checkpoint-sqlite==3.1.2`（三包同钉，抬版须人工评估；理由见 [`requirements.txt`](../requirements.txt) 注释） |
| Web | `fastapi>=0.162,<0.163` · `starlette>=1.3.1,<2` · `uvicorn[standard]>=0.30,<0.31` · `pydantic>=2.7,<3` · `pydantic-settings>=2.3,<3` |
| 其他 | `openai>=1.30,<2` · `httpx>=0.27,<0.29` · `duckduckgo-search>=4,<5` · `pyyaml>=6,<7` · `mcp>=1.2,<2`（`--no-deps` 安装 + overrides，防抬 uvicorn/starlette） |
| 前端 | React 18 · Vite 8 · TS 5 · Tailwind 4 · zustand 4（`npm ci` 口径） |
| 质量门禁工具链 | `requirements-dev.txt` 钉 ruff / mypy / pre-commit；配置在 `pyproject.toml` |

### 6.2 启动形态

```bash
qianfan                       # 日常：uv tool install -e . --overrides overrides.txt，单后台 + 自动开浏览器
python start.py               # 开发：改前端时用（后端 --reload + Vite 热重载）；与 qianfan 互斥
uvicorn backend.main:app --port 8000 --reload   # 手工起后端
```

一台机器**只共用一个后台**：第二个 `qianfan` 只把浏览器开到已在跑的后台并退出；关掉启动终端 = 所有任务停下（可续说/续跑）。`frontend/dist` 由后端同源托管；产物缺失或比 `frontend/src` 旧时 `qianfan` 会提示先构建。

### 6.3 运行态数据布局

| 路径 | 内容 | 备注 |
|---|---|---|
| `data/tasks.json` | 任务记录（原子写，损坏自动备份） | 唯一任务记录源 |
| `data/models.json` | 模型档案（**不含 Key**） | 全局默认 / 每任务模型 id |
| `~/.qianfan/credentials.json` | 界面粘贴的 Key | 仓库外；接口只回 `key_set` / `key_source` |
| `data/traces/<task_id>.jsonl` | 事件 + trace 时间线 | `trace_end` 只在这里 |
| `data/checkpoints/checkpoints.sqlite` | LangGraph checkpoint | 格式戳 v2；v1 拒绝挂载 |
| `data/logs/` | 结构化日志 | |
| `<任务目录>/.qianfan/plans/<task_id>.md` | 计划文件 | 任务工作区内，唯一来源 |

### 6.4 CI 与验证入口

- CI 三个 job（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)）：`backend-test`（ruff + mypy + pytest 离线全量）、`backend-windows-smoke`（Windows 敏感面）、`frontend-build`（`npm ci` → typecheck → `npm test` → `check:sse` → build；job 显示名沿用「Frontend typecheck & build」——分支保护锁定了这个 required check 的字符串）。
- 本地权威命令：`.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q`、`-m ruff check backend scripts`、`-m mypy`；前端 `npm run typecheck` / `npm run check:sse` / `npm run build`。
- 文档链接门：`python scripts/check_doc_links.py`（只校验 tracked Markdown 的相对链接）。
- 发布前 / 换供应商：按 [`docs/manual-verification.md`](manual-verification.md) 的 9 个场景启服务、在网页端手动跑一遍（CI 不跑真模型）。

---

## 7. 安全边界（这不是安全边界）

`qianfan` 只监听回环、Host 检查只认 `127.0.0.1` / `localhost`（挡 DNS 重绑定）、CORS 只放行自身来源——这些是**减少暴露面**，不是沙箱。完整口径与审计证据见 [`README.md`](../README.md)「这不是安全边界」与 [`security-audit-2026-10-19-bash.md`](archive/security-audit-2026-10-19-bash.md)。

- **默认没有登录**：同机其他进程/用户可直接驱动 API；`auth_enabled` 只挡无凭据访问，不是权限边界。远程访问不在本章范围。
- **`bash` 拿运行账号权限**：工作目录只是起点，`data/`、`.git`、`.env`、家目录与网络都可读写；trace 不是防篡改审计日志。撤销靠 git。
- **子进程只继承白名单环境变量**（挡「继承」，不挡读文件）；停止 / 超时回收本次调用建立的进程组 / Job Object，自行 `setsid` 脱离的进程不受管。
- **只限时**（默认 30s，1–600s）+ 截断输出尾部；**没有** CPU / 内存 / 磁盘 / 进程数上限。
- `policy_rules` 空表 = 不拦；它拦的是「工具调用入参」，不是 bash 内部行为。
- 需要真隔离就放进容器或虚拟机（本章不提供 Docker 启动方式；C4 已删 `Dockerfile` 与 `docker-compose.yml`）。
- 完整暴露面、密钥面与已知 CVE 缓解见 [`docs/security.md`](security.md) §1/§2/§6/§7；本节只保留架构视角，不复述细节。

---

## 8. 文档与验证入口

| 想知道 | 去这里 |
|---|---|
| 能力清单 / 快速开始 / 模型与工作区形态 | [`README.md`](../README.md) |
| 跨任务硬规则、任务路由、验证入口 | [`AGENTS.md`](../AGENTS.md) |
| 决策与理由（现役 5 份 + 退役 5 份） | [`docs/adr/`](adr/) |
| 交付历史（五轮迭代 + 切片） | [`docs/archive/OVERVIEW.md`](archive/OVERVIEW.md)、[`docs/archive/pivot-plan-build.md`](archive/pivot-plan-build.md) |
| LangGraph 0.2 → 1.2.x 迁移实测 | [`docs/migration-langgraph-1x.md`](migration-langgraph-1x.md) |
| stop / resume / durability 说明 | [`docs/incremental-arch-p3-resume.md`](incremental-arch-p3-resume.md) |
| 已交付 / 已退役的 spec | [`docs/specs/`](specs/)（冻结归档，逐份带状态头） |
| 发布前真模型验收 | [`docs/manual-verification.md`](manual-verification.md) |
| 配置键与默认值 | [`.env.example`](../.env.example) + [`backend/config.py`](../backend/config.py) |
| 文档归属与归档规则 | [`docs/agents/domain.md`](agents/domain.md) |
