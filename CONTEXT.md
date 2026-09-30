# langgraph-agent

自主任务 Agent 的运行时：自然语言任务 → planner → executor → tool → reflect 循环 → 工具调用 → SSE 可视化。
本文件是本上下文的统一语言（glossary）。只定义术语，不写实现。

## Language

### 循环单位

**task（任务）**：
一次自然语言请求的完整执行单位，拥有状态、计划、回合记录、产物与终态。

**step（回合）**：
循环的一次迭代——planner → executor →（tool）→ reflect。`step_index` 数的是它，`max_steps` 卡的是它。
_Avoid_: 步骤（本仓「步骤」同时被用来指 step 与 plan step，两个不同的东西）

**plan step（计划项）**：
planner 声明的意图条目（一句短描述 + 状态），可以有多个，与回合数不必一一对应。
_Avoid_: 步骤

**tool call（工具调用）**：
executor 要求执行的一次工具动作；状态是 `pending | success | failed | skipped` 之一。`pending` = 未收口（不是终态；同 :48 的 dangling 判据）——已执行/已失败/已被闸门跳过三档才是终态，见 `nodes.py` 的 `TERMINAL_TOOL_CALL_STATUSES`（#100）
_Avoid_: 动作

### 完成与验收

**claimed complete（声称完成）**：
模型给出了 `final_answer`。这只是宣称，不是事实——它与「验证通过」相互独立。

**verified（验证通过）**：
完成验证对可机械判定的证据复核后判定通过。

**informational task（信息型任务）**：
交付物就是回答本身、不需要改动外部世界的任务。

**deliverable task（交付型任务）**：
交付物是外部世界的改动（文件、副作用）的任务。
_Avoid_: 动作类步骤、动作类任务

**expectation（声明式预期）**：
调用方在启动任务时声明的、可机械判定的完成条件。由调用方给，不由模型自报。
_Avoid_: 验收标准（那是 grader 的主观 rubric）、预期产物

**artifact（产物）**：
本任务（含子任务）**写出**的文件；读到的文件不是产物。

**dangling tool call（悬空 tool_call）**：
一条 assistant 的 tool_call 没有对应的工具结果——状态永远停在 `pending`，消息里也没有配对的 `role: tool` 回答。

### 闸门与韧性

**confirm gate（确认闸门）**：
危险工具执行前必须经人判决的闸门；未批准的收口分三档（明确拒绝 / 超时未答 / 停止打断），不是一个布尔。
_Avoid_: 审批、授权

**circuit breaker（熔断）**：
工具连续失败后的冷却；计数按运行各记一本账，不跨父任务与子任务共享。

**subtask（子任务）**：
由 `spawn_subagent` 工具派生的隔离运行；不进父任务的活跃账本，结果 fold 回父任务的子任务摘要。
