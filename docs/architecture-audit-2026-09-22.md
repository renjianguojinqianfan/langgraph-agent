# 架构审计 — 2026-09-22（只读）

> **点-时记录**：结论与 `file:line` 以 `db0c9f5`（2026-09-22）为准；此后提交会让行号漂移。
> **口径**：只读审计（未改任何文件）+ 对照 33 份真实 trace；总体判定 **high_risk**。
> **出处**：以 `agent-architecture-audit` 技能（MIT）执行；trace 证据来自配套评测台（私有仓库 `eval-harness`，
> 记录见其 `docs/records/impl-log.md`，含 T056 / T069 / T105 等活标本）。
> **用途**：本文件是审计的**存档**（发现 → 票的映射与状态）；**权威跟踪在 GitHub Issues**，不在此维护第二份 backlog。

## 一句话结论

主要失效模式：**agent 对「做完了」的自我认定只验结构性（产物存在且非空），不验语义（对不对、全不全）**
——于是它能自信地交付错的东西，而真实故障又被降级成文本、根因不可见。

## 发现（按严重度）

| # | 级别 | 发现 | 证据（`file:line` @ `db0c9f5`） | 置信 | 票 |
|---|---|---|---|---|---|
| 1 | 🔴 critical | **完成验证只验「产物存在且非空」**：`reflect._verify_completion` 仅查 S1（产物存在非空）+ S2（全失败却声称完成）；docstring 自认不查 completeness；验证失败且重试耗尽 → **降级成 `COMPLETED(degraded)`，不判 FAILED** | `backend/core/agent/nodes.py:669-709`、`:644-650`（另 `backend/services/task_manager.py:744-750` 补标记） | 0.9 | [#46](https://github.com/renjianguojinqianfan/langgraph-agent/issues/46) |
| 2 | 🟠 high | **模型调用失败被降级成文本、次生异常吃掉根因**：planner 异常 → `plan=["Planner error: …"]`（纯字符串）且不设 `state["error"]`；收尾 `PlanStep(**p)` 对 str 抛 `TypeError`，顶掉真实 403/断网 | `backend/core/agent/nodes.py:245-247`、`backend/services/task_manager.py:737` | 1.0 | [#12](https://github.com/renjianguojinqianfan/langgraph-agent/issues/12) |
| 3 | 🟠 high | **MCP 逐调用确认判定「失败即放行」（fail-open）**：`_needs_confirm(args)` 抛异常只 warning，`need_confirm` 退回静态 `requires_confirm`（MCP 默认 False）→ 写类 MCP 调用不经确认直接执行 | `backend/core/agent/nodes.py:429-440`（旧注释原文 `it never blocks execution`） | 0.8 | [#47](https://github.com/renjianguojinqianfan/langgraph-agent/issues/47) |
| 4 | 🟡 medium | **工具纪律只写在提示词**：`EXECUTOR_SYSTEM` 要求「需要就调工具」，代码不强制；不调工具直接进 final_answer，S2 只能抓「全失败」 | `backend/core/agent/prompts.py:21-26`、`backend/core/agent/nodes.py:476-483` | 0.8 | [#48](https://github.com/renjianguojinqianfan/langgraph-agent/issues/48) |
| 5 | 🟡 medium | **上下文压缩会反复空转**：truncate 固定 `keep_recent=10`；若这 10 条本身超预算，每轮触发又压不下去 → 事件刷屏（trace 实测一轮 20 次 `context_compressed` 并自崩） | `backend/core/agent/context.py:208-231, 259-303`（本机复现：预算 1000 下连续三轮 `compressed=True, dropped=2, tokens=10015` 恒定） | 0.6 | [#49](https://github.com/renjianguojinqianfan/langgraph-agent/issues/49) |
| 6 | 🟡 medium | **`code_exec` 不是沙箱且把密钥暴露给 agent**：自述 basic sandbox（无 syscall 过滤 / cgroup）；`subprocess` 继承 `os.environ`（含 `LLM_API_KEY`） | `backend/utils/sandbox.py:1-14, 22-66`（`:37` `env = dict(os.environ)`） | 0.9 | [#50](https://github.com/renjianguojinqianfan/langgraph-agent/issues/50) |
| 7 | ⚪ low | **子任务摘要折叠可能造成上下文重复**：`_fold_subtask_summaries` 以 `assistant` 角色追加进历史 | `backend/core/agent/nodes.py:386-390` | 0.4 | [#51](https://github.com/renjianguojinqianfan/langgraph-agent/issues/51) |

> 同日另有一票来自评测台实测（非本审计）：[#45](https://github.com/renjianguojinqianfan/langgraph-agent/issues/45)
> —— OpenAI 兼容客户端不支持 DeepSeek 官方端点的 thinking / `reasoning_content`（多轮 400）。

## 架构诊断

三层**同源偏差**：

1. **完成语义偏软** —— 把「产物存在」当「完成」（发现 1、4）；
2. **失败语义偏软** —— 模型故障降级成文本、确认判定失败即放行（发现 2、3）；
3. **纪律靠提示词** —— 工具纪律不落代码（发现 4）。

本质是**把「模型说了」当事实**，缺代码级的确定性契约。第 1、2 层互为放大器：失败被吞 → 完成被高估。

## 建议修复顺序（code-first，按「最便宜先做」）

1. planner 失败**如实写 `state["error"]`**、收尾不抛（= #12，改动最小）
2. 确认闸门 **fail-closed**（= #47，一行级）
3. **完成验证升级为「对照声明的交付物」**（= #46，能力提升正主；需先定义「动作类步骤 / 声明式预期产物」口径）
4. 工具纪律代码化（= #48）
5. 压缩空转设上限（= #49）
6. 子进程剔敏感环境变量（= #50）

## 修复状态（截至落盘时）

| 票 | 状态 |
|---|---|
| #12 | **已修** —— [PR #52](https://github.com/renjianguojinqianfan/langgraph-agent/pull/52)：planner 异常写 `state["error"]` + 空 `plan`，两条 planner 路由在 `error` 时短路到 `finish`；评审补充：子任务执行器 `_exec_one` 按图终态折叠（失败不再被报成「完成 + 空摘要」） |
| #47 | **已修** —— 同上 PR：判定异常时 `need_confirm = True`（fail-closed），翻转旧测试断言与命名 |
| #46 / #48 / #49 / #50 / #51 | **开放**，见各自 issue |
