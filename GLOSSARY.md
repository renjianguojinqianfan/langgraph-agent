# GLOSSARY.md

> 本文件是**词汇表入口**，不是第二份词汇表。定义型权威在 `backend/core/agent/state.py`
> （状态字段与节点名，图拓扑见 `backend/core/agent/graph.py`）与 `AGENTS.md`「跨任务安全边界」表；
> 已定居决策在 `docs/adr/`（现役 0001 / 0002（部分退役）/ 0008 / 0009 / 0010 / 0011 / 0012；0003 随 C2 退役，0004 随 C5 退役，0005–0007 随 0008 退役）。本文件只放指针与一行速查；技能（tdd /
> diagnosing-bugs / wait-what / domain-modeling / triage …）读完本文件，按指针去读权威源。
> 单上下文仓库：无 `GLOSSARY-MAP.md`。

## 指针

| 想知道什么 | 去哪 |
|---|---|
| 节点 / 状态字段术语的定义 | `backend/core/agent/state.py`（节点名见 `graph.py`） |
| 硬规则与边界不变量 | `AGENTS.md`「跨任务安全边界」表 |
| 已定居的决策与理由 | `docs/adr/0001`–`0012`（0002 部分退役；0003 随 C2 退役；0004 随 C5 退役；0005–0007 随 0008 退役；0011 模型配置双协议；0012 能力包与预设，均保留历史） |
| 计划 / 执行两模式、停下、交付 | [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)（决策 1–8） |
| 技能组配置（tracker / 标签 / 本文档的消费规则） | `docs/agents/` 三件套 |

## 速查术语（权威定义以上方指针为准）

图节点：`agent`（唯一的 LLM 节点，两种模式共用）/ `tool` / `finish`。
模式：`plan`（计划模式：`explore` 只读档 + 计划 prompt，产出计划文件）/ `build`（执行模式：全部已挂载工具）。
任务是开关的载体，**开关黏性、默认 build**；模式不是第二个节点，也不是每条消息选一次（ADR 0008 决策 2 / 3）。
状态三值：`RUNNING`（跑着）/ `INTERRUPTED`（停下，等你说话）/ `FAILED`（出错）——**没有「完成」终态**。
「一轮」= 一次 `invoke`（START→END，一条续说）；用户每发一句话开一轮，`StepRecord.round` 记这一步属于哪一轮（1 起，0 = #265 前的旧记录）；「步」= 一轮内的一次 `agent` 调用（`round_steps` 按步计预算）。一次 `agent` 调用返回时没有工具调用，这一轮就结束（停下，决策 4）。
「续说」= 停下后 `POST /api/tasks/{id}/messages`，沿同一 `thread_id` / checkpoint 接着跑，不是重开任务（决策 1）。
「交付」= 停下时人去看工作区里的文件，不是新状态（决策 8）。
运行时概念：`checkpoint` / `resume` / `workspace`（任务目录）/ 运行上下文 / 两层注入 / 模型配置。
能力包 / 预设：**能力包** = 本仓代码的静态工具面声明（`files` / `bash` / `skills` / `web-search` / `subagent` / `mcp`：工具名集合 + 注入片段 + 是否连 MCP + 是否影响子代理面）；**预设** = 全局组合（`minimal` / `research` / `full`，默认 `full` = #67 前行为）。全局、服务级、重启生效；关掉是真关（不实例化 / 不进 schema / 不进 dispatch / 不加注入 / 不连 MCP；不承诺模块不被 import）；子代理实际面 = 固定 tier ∩ 已启用包。保存态在 `data/plugins.json`（`{preset, overrides, exists}`；设置页「能力」区 / `PUT /api/plugins` 写，重启生效）；优先级 = CLI `--preset`（一次性，不写文件）> `data/plugins.json`（存在即权威）> `PLUGINS_PRESET` env（仅文件缺失时回退）> 默认 `full`；`GET /api/plugins` 的 `active.tools` 是实际装配面（含 legacy 开关与已连接 MCP 动态工具）。权威见 [ADR 0012](docs/adr/0012-plugin-packages-and-presets.md) 与 `backend/core/capability_packages.py` / `backend/services/plugins_store.py`。
「模型配置」= `data/models.json` v2 里的 **Provider**（id、名称、协议、API 地址、Key 环境变量名、是否预设）+ **模型条目**（id、所属 Provider、模型名、显示名、来源 `manual` / `migrated`）；`Task.model` 存模型条目的 id，空 = 全局默认（`default` 也指向模型条目 id）；取 Key 顺序 = 粘贴（`~/.qianfan/credentials.json`，按 Provider id）> 该 Provider 的环境变量，接口只回 `key_set` / `key_source`（pasted / env / none）；预设只有 DeepSeek。`protocol` 取 `openai-compatible` / `anthropic`，两种都可创建与使用（Anthropic 为非流式适配器；`thinking` / `reasoning_effort` 可配置，但 Anthropic 路径写入即拒，见 ADR 0011）；D-36 采样参数 `temperature` / `max_tokens` / `thinking` / `reasoning_effort` 挂模型条目、Provider 可给默认值。v1 扁平 profile 首次加载自动迁移为同 id 的 Provider + 模型条目（`source: migrated`），迁移前备份 `models.json.bak-v1`，幂等（ADR 0010 决策 7 经 ADR 0011 修订；数据结构 v2 见 `docs/architecture.md` `3.3）。
工作区：`Task.workspace`（绝对路径，存储前用 `normalize_workspace` 解析 `~`、相对路径、`..` 与符号链接；比较用 `workspace_key`，Windows 上再折叠盘符大小写与斜杠方向，所以同一目录的不同写法过滤结果一致）。新建任务取页面 `?dir=`，缺省为后台启动目录；旧任务补 `data/artifacts`。文件工具、`bash`、`load_skill` 与注入块都从运行上下文（`backend/core/runtime_context.py`）取这个目录；子代理继承父任务的目录（ADR 0010 决策 1）。
任务：`thread_id`（= task_id，resume 与续说寻址恒等）；工作区文件列表 = 任务目录里按 `.gitignore` 可见的文件（C5，无产物账本）。
工具调用状态：`pending | success | failed | skipped`——只有后三是终态（`nodes.py` 的 `TERMINAL_TOOL_CALL_STATUSES`，#100）；`pending` 未收口即 `dangling tool call`（消息面也没有配对的 `role: tool` 回答）。
危险动作：`policy_rules` 规则表 + `agent` 里那一个钩子；命中 → `skipped` 记录 + `{block, reason}` 回模型（ADR 0008 决策 6）。**默认空表 = 不拦**，它不是安全边界（见 README「这不是安全边界」）。
`skipped` 的第二个来源：死进程收尾——启动对账 / 终态写入把遗留 `pending` 调用收为 `skipped`，`error` 写中断说明；收尾本身不写对话面，续跑时由 `agent` 入口的悬空清扫补一条 `role:"tool"` 回答（#317）。
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
