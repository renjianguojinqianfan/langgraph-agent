# GLOSSARY.md

> 本文件是**词汇表入口**，不是第二份词汇表。定义型权威在 `backend/core/agent/state.py`
> （状态字段与节点名，图拓扑见 `backend/core/agent/graph.py`）与 `AGENTS.md`「跨任务安全边界」表；
> 已定居决策在 `docs/adr/`（现役 0001 / 0002（部分退役）/ 0008 / 0009 / 0010；0003 随 C2 退役，0004 随 C5 退役，0005–0007 随 0008 退役）。本文件只放指针与一行速查；技能（tdd /
> diagnosing-bugs / wait-what / domain-modeling / triage …）读完本文件，按指针去读权威源。
> 单上下文仓库：无 `GLOSSARY-MAP.md`。

## 指针

| 想知道什么 | 去哪 |
|---|---|
| 节点 / 状态字段术语的定义 | `backend/core/agent/state.py`（节点名见 `graph.py`） |
| 硬规则与边界不变量 | `AGENTS.md`「跨任务安全边界」表 |
| 已定居的决策与理由 | `docs/adr/0001`–`0010`（0002 部分退役；0003 随 C2 退役；0004 随 C5 退役；0005–0007 随 0008 退役，均保留历史） |
| 计划 / 执行两模式、停下、交付 | [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)（决策 1–8） |
| 技能组配置（tracker / 标签 / 本文档的消费规则） | `docs/agents/` 三件套 |

## 速查术语（权威定义以上方指针为准）

图节点：`agent`（唯一的 LLM 节点，两种模式共用）/ `tool` / `finish`。
模式：`plan`（计划模式：`explore` 只读档 + 计划 prompt，产出计划文件）/ `build`（执行模式：全部已挂载工具）。
任务是开关的载体，**开关黏性、默认 build**；模式不是第二个节点，也不是每条消息选一次（ADR 0008 决策 2 / 3）。
状态三值：`RUNNING`（跑着）/ `INTERRUPTED`（停下，等你说话）/ `FAILED`（出错）——**没有「完成」终态**。
「一轮」= 一次 `invoke`（START→END，一条续说）；「步」= 一轮内的一次 `agent` 调用（`round_steps` 按步计预算）。一次 `agent` 调用返回时没有工具调用，这一轮就结束（停下，决策 4）。
「续说」= 停下后 `POST /api/tasks/{id}/messages`，沿同一 `thread_id` / checkpoint 接着跑，不是重开任务（决策 1）。
「交付」= 停下时人去看工作区里的文件，不是新状态（决策 8）。
运行时概念：`checkpoint` / `resume` / `workspace`（任务目录）/ 运行上下文 / 两层注入 / 模型配置。
「模型配置」（ADR 0010 决策 7）= `data/models.json` 里的一套 OpenAI 兼容配置（id、名称、地址、模型名、环境变量名、是否预设）；`Task.model` 存配置 id，空 = 全局默认；取 Key 顺序 = 粘贴（`~/.qianfan/credentials.json`）> 该配置的环境变量，接口只回 `key_set` / `key_source`（pasted / env / none）；预设只有 DeepSeek。
工作区：`Task.workspace`（绝对路径，存储前用 `normalize_workspace` 解析 `~`、相对路径、`..` 与符号链接；比较用 `workspace_key`，Windows 上再折叠盘符大小写与斜杠方向，所以同一目录的不同写法过滤结果一致）。新建任务取页面 `?dir=`，缺省为后台启动目录；旧任务补 `data/artifacts`。文件工具、`bash`、`load_skill` 与注入块都从运行上下文（`backend/core/runtime_context.py`）取这个目录；子代理继承父任务的目录（ADR 0010 决策 1）。
任务：`thread_id`（= task_id，resume 与续说寻址恒等）；工作区文件列表 = 任务目录里按 `.gitignore` 可见的文件（C5，无产物账本）。
工具调用状态：`pending | success | failed | skipped`——只有后三是终态（`nodes.py` 的 `TERMINAL_TOOL_CALL_STATUSES`，#100）；`pending` 未收口即 `dangling tool call`（消息面也没有配对的 `role: tool` 回答）。
危险动作：`policy_rules` 规则表 + `agent` 里那一个钩子；命中 → `skipped` 记录 + `{block, reason}` 回模型（ADR 0008 决策 6）。**默认空表 = 不拦**，它不是安全边界（见 README「这不是安全边界」）。
注入：`两层注入`（home 层 `AGENTS.md` → 任务目录 `AGENTS.md`；实现 `inject.py`）。
记录：`trace`（系统侧事件/轨迹 JSONL，可回放；**不进** LLM 上下文——压缩留痕里只放指针）——与 `transcript` 是两回事，勿混。

## 术语澄清（domain-modeling 定居，2026-10-07）

**`plan` 有两个意思，禁用裸词「计划」**：

- 任务的计划文件（运行时对象，C6）：`<任务目录>/.qianfan/plans/<task_id>.md`，唯一来源。计划模式的一轮产出它
  （模型给 Markdown、系统写文件），用户停下时可整份编辑（`PUT /api/tasks/{id}/plan` 写回文件，外部编辑器同理）；
  执行时以一条消息进入对话，文件变了就换那条，版本记在 `Task.plan_version`。不再有 `PlanStep` 列表 /
  `plan_update` 事件 / system 尾部计划块。
- 票面计划（工作级）：issue/spec 层的拆解与 AC。指它时写「票面 / spec」。

## 禁用词（`_Avoid_`，domain-modeling 定居；原 `CONTEXT.md` 收编而来）

指这些概念时**不要**用左边的说法——它们要么歧义，要么掩盖了已定居的决策：

| 想说 | 别用 | 为什么 |
|---|---|---|
| `step`（一次 `agent` 调用） | 裸用「步骤」 | 会和计划文件里编号的步骤混淆；指前者写 `step` / 一次 `agent` 调用，指后者写「计划里的步骤」 |
| `plan mode` / `build mode` | 「planner 节点」「executor 节点」 | 两节点已合并成一个 `agent`（#139）；再说节点名会把模式误当拓扑 |
| 一轮结束 / 停下 | 「完成」「COMPLETED」 | 没有完成终态（ADR 0008 决策 5）；`COMPLETED` 只是旧记录，加载时映射成 `INTERRUPTED` |
| 一次工具动作 | 「动作」 | 与已退役的「动作类步骤」撞词（ADR-0006 推翻了这个分类） |
| `deliverable task` | 「动作类任务 / 动作类步骤」 | 该分类由模型自报，2026-10-01 已退役 |

## 外来术语对照（对标 pi 等外部 agent 时用；不是本仓词）

| 外来词 | 外部含义 | 本仓对应 |
|---|---|---|
| `transcript` | 喂给模型的会话正文（pi：session 的 active branch，compaction 之后仍是它） | `AgentState.messages`（经 eviction / compression 之后） |
| `session` | 持久化的会话文件/树（pi 存 `~/.pi/agent/sessions/`，可 `/fork` `/tree` 分支） | 无直接对应；最接近的是 `thread_id` 寻址的 checkpoint 状态链 |
| `compaction` | 压缩旧历史、保近期，原条目不删（pi sessions.md 原话） | `context.py` 的 `evict_tool_results` + `compress_messages` |

> 新术语由 `/domain-modeling` 在定居时按上方格式加一项；不要把定义抄进本文件。
