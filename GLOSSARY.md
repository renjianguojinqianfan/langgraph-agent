# GLOSSARY.md

> 本文件是**词汇表入口**，不是第二份词汇表。定义型权威在 `backend/core/agent/state.py`
> （状态字段与节点名，图拓扑见 `backend/core/agent/graph.py`）与 `AGENTS.md`「跨任务安全边界」表；
> 已定居决策在 `docs/adr/`（现役 0001–0007）。本文件只放指针与一行速查；技能（tdd /
> diagnosing-bugs / wait-what / domain-modeling / triage …）读完本文件，按指针去读权威源。
> 单上下文仓库：无 `GLOSSARY-MAP.md`。

## 指针

| 想知道什么 | 去哪 |
|---|---|
| 节点 / 状态字段术语的定义 | `backend/core/agent/state.py`（节点名见 `graph.py`） |
| 硬规则与边界不变量 | `AGENTS.md`「跨任务安全边界」表 |
| 已定居的决策与理由 | `docs/adr/0001`–`0007` |
| 技能组配置（tracker / 标签 / 本文档的消费规则） | `docs/agents/` 三件套 |
| 「完成与验收」这条链的定义（claimed→verified→UNVERIFIED / expectation / rubric / grader） | `docs/adr/0005`（终态）、`0006`（expectation 归属）、`0007`（grader 与裁判故障） |

## 速查术语（权威定义以上方指针为准）

图节点：`planner` / `executor` / `tool` / `reflect` / `risk_scan` / `human_confirm` / `finish`。
运行时概念：`checkpoint` / `resume` / 确认闸门（confirm gate）/ 熔断 / 沙箱根。
任务与产物：`artifact`（产物，ADR-0004：本任务**写出**的文件，仅 `registers_artifact` 工具为源）/ `expectation`（调用方声明的**机械**完成条件，ADR-0006）/ `rubric`（调用方声明的**语义**合格线，自由文本、交独立裁判判，ADR-0007）/ `grader`（判 rubric 的那个 `aux_llm` 客户端，**必须独立于声明者**，ADR-0007）/ `UNVERIFIED`（跑完但验收未过的独立终态，ADR-0005）/ `thread_id`（= task_id，resume 寻址恒等）。
完成这条链上的四个不同概念，别互相代称：`claimed complete`（模型给了 `final_answer`，只是宣称）→ `verified`（机械证据复核通过）→ `UNVERIFIED`（跑完了但没通过）；`informational task`（交付物就是回答）对 `deliverable task`（交付物是外部世界的改动）。
工具调用状态：`pending | success | failed | skipped`——只有后三是终态（`nodes.py` 的 `TERMINAL_TOOL_CALL_STATUSES`，#100）；`pending` 未收口即 `dangling tool call`（消息面也没有配对的 `role: tool` 回答）。
闸门终态五档：`approved` / `denied` / `timed_out` / `aborted` / `auto_approved`（常量与分档判据以 `nodes.py` 的 `CONFIRM_*` 为准）。
人工复核：`Task.review`（#46 第二步切片 B）——把 `UNVERIFIED` 接受为 `COMPLETED` 的那次人工作证，带 who/when/why/`overruled_reason`；它是 `verification` 的**兄弟字段**，机器原判不被改写（改了就成 ADR 0005 要防的橡皮图章）。`reviewer` 自报，因为平台无用户账号（token 只认证「能进门」，不认证「是谁」）。
图形态：`subtask`（子任务图：无 risk/confirm 节点，工具面走声明档位——`graph.py` `mode="subtask"`）。
注入：`两层注入`（home 层 `AGENTS.md` → 工作区根 `AGENTS.md`；实现 `inject.py`）。
记录：`trace`（系统侧事件/轨迹 JSONL，可回放；**不进** LLM 上下文——压缩留痕里只放指针）——与下面的 `transcript` 是两回事，勿混。

## 术语澄清（domain-modeling 定居，2026-10-07）

**`plan` 有两个意思，禁用裸词「计划」**：

- `state["plan"]`（运行时对象）：planner 节点产出的 PlanStep 列表。消费者是 `risk_scan`、`plan_update`
  事件，以及 **#119 起** executor 的 system 尾部渲染块（`# Current Plan`，替换语义、不参与压缩淘汰）——
  它曾是「planner 花 ~10s 产出、执行面一个字都不读」的断供面（2026-10-07 源码实证，对标见
  `docs/research-pi-1.0.md` §2；那条实证到 #119 落地为止，别照抄）。指它时必须写全 `state["plan"]`。
- 票面计划（工作级）：issue/spec 层的拆解与 AC。指它时写「票面 / spec」。

## 禁用词（`_Avoid_`，domain-modeling 定居；原 `CONTEXT.md` 收编而来）

指这些概念时**不要**用左边的说法——它们要么歧义，要么掩盖了已定居的决策：

| 想说 | 别用 | 为什么 |
|---|---|---|
| `step`（回合）/ `plan step`（计划项） | 裸用「步骤」 | 本仓「步骤」同时指两者；`plan` 也禁用裸词「计划」（见上一节） |
| 一次工具动作 | 「动作」 | 与已退役的「动作类步骤」撞词（ADR-0006 推翻了这个分类） |
| `deliverable task` | 「动作类任务 / 动作类步骤」 | 该分类由模型自报，2026-10-01 已退役 |
| `UNVERIFIED` | 「降级完成」「`degraded`」「软失败」 | ADR-0005 退役了这三个说法（`degraded` 布尔已换成 `verification.reason`） |
| `rubric` | 「验收标准」 | 那是 grader 的**主观**判据；`expectation` 才是机械的那一份 |
| `grader` | 「自评」「verification model」「打分器」 | 主模型自评正是 P0-B 请走的结构性幻觉通道——名字不能把它请回来 |
| `confirm gate` | 「审批」「授权」 | 未批准收口分三档，不是一个布尔；「审批」暗示只有一个开关 |

## 外来术语对照（对标 pi 等外部 agent 时用；不是本仓词）

| 外来词 | 外部含义 | 本仓对应 |
|---|---|---|
| `transcript` | 喂给模型的会话正文（pi：session 的 active branch，compaction 之后仍是它） | `AgentState.messages`（经 eviction / compression 之后） |
| `session` | 持久化的会话文件/树（pi 存 `~/.pi/agent/sessions/`，可 `/fork` `/tree` 分支） | 无直接对应；最接近的是 `thread_id` 寻址的 checkpoint 状态链 |
| `compaction` | 压缩旧历史、保近期，原条目不删（pi sessions.md 原话） | `context.py` 的 `evict_tool_results` + `compress_messages` |

> 新术语由 `/domain-modeling` 在定居时按上方格式加一项；不要把定义抄进本文件。
