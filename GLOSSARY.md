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

> 新术语由 `/domain-modeling` 在定居时往速查行加一项；不要把定义抄进本文件。
