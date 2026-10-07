# GLOSSARY.md

> 本文件是**词汇表入口**，不是第二份词汇表。定义型权威在 `backend/core/agent/state.py`
> （状态字段与节点名，图拓扑见 `backend/core/agent/graph.py`）与 `AGENTS.md`「跨任务安全边界」表；
> 已定居决策在 `docs/adr/`（现役 0001–0004）。本文件只放指针与一行速查；技能（tdd /
> diagnosing-bugs / wait-what / domain-modeling / triage …）读完本文件，按指针去读权威源。
> 单上下文仓库：无 `GLOSSARY-MAP.md`。

## 指针

| 想知道什么 | 去哪 |
|---|---|
| 节点 / 状态字段术语的定义 | `backend/core/agent/state.py`（节点名见 `graph.py`） |
| 硬规则与边界不变量 | `AGENTS.md`「跨任务安全边界」表 |
| 已定居的决策与理由 | `docs/adr/0001`–`0004` |
| 技能组配置（tracker / 标签 / 本文档的消费规则） | `docs/agents/` 三件套 |

## 速查术语（权威定义以上方指针为准）

图节点：`planner` / `executor` / `tool` / `reflect` / `risk_scan` / `human_confirm` / `finish`。
运行时概念：`checkpoint` / `resume` / 确认闸门（confirm gate）/ 熔断 / 沙箱根。
任务与产物：`artifact`（产物，ADR-0004：本任务**写出**的文件，仅 `registers_artifact` 工具为源）/ `degraded`（完成验证重试耗尽的降级完工标记）/ `thread_id`（= task_id，resume 寻址恒等）。
闸门终态五档：`approved` / `denied` / `timed_out` / `aborted` / `auto_approved`（常量与分档判据以 `nodes.py` 的 `CONFIRM_*` 为准）。
图形态：`subtask`（子任务图：无 risk/confirm 节点，工具面走声明档位——`graph.py` `mode="subtask"`）。
注入：`两层注入`（home 层 `AGENTS.md` → 工作区根 `AGENTS.md`；实现 `inject.py`）。
记录：`trace`（系统侧事件/轨迹 JSONL，可回放；**不进** LLM 上下文——压缩留痕里只放指针）——与下面的 `transcript` 是两回事，勿混。

## 术语澄清（domain-modeling 定居，2026-10-07）

**`plan` 有两个意思，禁用裸词「计划」**：

- `state["plan"]`（运行时对象）：planner 节点产出的 PlanStep 列表。现役唯一消费者是
  `risk_scan` 与 `plan_update` 事件，**不进 executor 上下文**（2026-10-07 源码实证，
  见 `docs/research-pi-1.0.md` §2 的对标）。指它时必须写全 `state["plan"]`。
- 票面计划（工作级）：issue/spec 层的拆解与 AC。指它时写「票面 / spec」。

## 外来术语对照（对标 pi 等外部 agent 时用；不是本仓词）

| 外来词 | 外部含义 | 本仓对应 |
|---|---|---|
| `transcript` | 喂给模型的会话正文（pi：session 的 active branch，compaction 之后仍是它） | `AgentState.messages`（经 eviction / compression 之后） |
| `session` | 持久化的会话文件/树（pi 存 `~/.pi/agent/sessions/`，可 `/fork` `/tree` 分支） | 无直接对应；最接近的是 `thread_id` 寻址的 checkpoint 状态链 |
| `compaction` | 压缩旧历史、保近期，原条目不删（pi sessions.md 原话） | `context.py` 的 `evict_tool_results` + `compress_messages` |

> 新术语由 `/domain-modeling` 在定居时按上方格式加一项；不要把定义抄进本文件。
