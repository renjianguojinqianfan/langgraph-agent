---
status: accepted
---

# 完成验证的终态：UNVERIFIED 独立于 COMPLETED / FAILED

> 2026-10-01 #46 拍板（第一步 2026-10-11 落地）。修订 `docs/specs/p0-b-completion-verification.md`
> §二 Q4 的**终态命名**，**不改变 Q4 的安全原则**。

## 决策

1. 「模型声称完成、但我方验证没通过」有独立终态 `UNVERIFIED`，不再伪装成 `COMPLETED`。
2. `COMPLETED` 收紧为「跑完 **且** 验证通过（或验证关闭）」；`FAILED` 保持 crash/异常语义；
   `INTERRUPTED` 不变。
3. 原因用 `Task.verification.reason` 区分：`retry_exhausted` / `max_steps` / `grader_error`
   （回环中是 `pending`；收口只发生在 `_finalize_terminal`——`finish` 不改写 reason）。
4. headless 退出码**扩展**：0/1/2/3 语义不变，新增 `4 = UNVERIFIED`。
5. 「绝不因验证误判判 FAILED」（Q4 核心）保留——`UNVERIFIED` 不是 `FAILED`。

## 为什么值得 ADR 级记录

终态是评测台（headless exit code + JSON）、API、前端徽章、trace 四处共用的边界。
把「没验过」和「崩了」分开，决定的是「虚假完工」能不能被机器识别——
这正是 #46 存在的理由，漏在这里就等于没修。

反面也要说清：为什么不改成「验证不过一律 FAILED」。`max_steps` 抢占会把「跑得久但正常」
判死、LLM 裁判（第二步）会误判、一个 `FAILED` 会同时表示「崩了 / 没验过 / 步数用完」，
三者都违背本仓「能分辨的必须分辨」的纪律（闸门三档、熔断各记一本账、评测台三态）。
外部实码同向：deepagents 的 `middleware/rubric.py` 把 `max_iterations_reached` /
`grader_error` 从 `failed` 里分出来，参照物做的正是「卷子不及格 ≠ 崩溃」。

## 影响面

- `backend/api/schemas.py` `TaskStatus`（五值 → 六值）、`Task.verification` 载荷；
- `backend/core/agent/nodes.py` `finish`（终态映射）与 `reflect`（`degraded` → `reason`）；
- `backend/services/task_manager.py` `_finalize_terminal`：新增 `task_unverified` 事件、
  `max_steps` 抢占时把 `pending` 兜底改写成 `reason="max_steps"`；
- `backend/headless.py`：`_TERMINAL` 加 `UNVERIFIED`、`EXIT_UNVERIFIED = 4`、
  `_exit_code_for` 映射；
- SSE 四处对齐（后端发布点 / `docs/architecture.md` §3.4 / `useSSE.ts` `EVENT_TYPES` /
  `types/index.ts` `SSEventType`，机械断言 `npm run check:sse`）；
- 前端：`TaskStatus` 联合、`taskStore` 终态 case、`HistoryPanel` / `TaskHeader` 徽章
  （orange，与 `INTERRUPTED` 的 amber 分开）；
- 既有测试重裁（逐条理由在 PR #46 正文）：`test_verify.py::test_degrade_after_max_retries`
  改名 `test_unverified_after_max_retries`，另 4 条断言父任务/子任务 COMPLETED 的用例改判
  UNVERIFIED——它们钉的是「degraded 章算完成」这个旧决定。

## 重开条件

引入「人工复核把 UNVERIFIED 接受为 COMPLETED」时重开：该操作算不算「验证通过」需要新拍板
（#46 规格把它列在 Out of Scope）。子任务侧另有一本账：`run_subtask` 目前把「非 COMPLETED」
一律折成 `failed`，`UNVERIFIED` 在父任务眼里读成「子任务失败了」——那是 [#106] 那一类
折叠语义的活，不在本 ADR 的终态定义里。
