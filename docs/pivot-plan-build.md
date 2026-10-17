# 转向落地：七片切法（计划/执行两模式取代完成验证栈）

> 🗄️ **切片历史（2026-10-17，S6 / #140）**：S0–S5 已合入；本文保留切片决策历史，不再代表现役实现。
> 现役形态见 [README](../README.md)「核心模型」与 [ADR 0008](adr/0008-single-loop-plan-build-modes.md)。

> 2026-10-14。决策本体见 [ADR 0008](adr/0008-single-loop-plan-build-modes.md)，**本文只记怎么切、每片怎么验**，
> 不复制决策理由。参照物调研见 [pi v1.2.0 agent 构建](research-pi-agent-construction.md)。

## 目标形态（一句话）

一个任务 = 一段多轮对话；一条 loop 一个 `agent` 节点；**默认执行模式**，用户显式切到计划模式才出计划；
一轮的最后一次 `agent` 调用没有工具调用就停下（球回到人手里）；状态只有**跑着 / 停下 / 出错**，没有「完成」；危险动作由设置里的
规则表 + 一个钩子当场拒掉。

## 顺序原则

- **S1 放最前**：这几处入口现在把「图的形状」与「退出码」钉着，先拆掉，后面改图不用回头改它们。
- **S2 在 S4 前**：先有新的拦截，再拆旧的闸门，中间不出现「裸奔」窗口。
- **S5 放最后**：新形态最依赖前面都清干净了。
- 每片都能单独合进 master 且可运行。

## 七片

| 片 | 做什么 | 完成判据 |
|---|---|---|
| **S0 治理先行**（零行为代码）〔已合 [#134](https://github.com/renjianguojinqianfan/langgraph-agent/pull/134)〕 | 新增 [ADR 0008](adr/0008-single-loop-plan-build-modes.md)；0002/0005/0006/0007 加退役标记；删 `ci.yml` 的 `guard-protected-files` job；`AGENTS.md` 不变量表按新形态重估 | CI 全绿（少一个 job）；文档里不再有「守卫着一个已不存在的闸门」的条目 |
| **S1 先砍评测入口**〔已合 [#142](https://github.com/renjianguojinqianfan/langgraph-agent/pull/142)；票面 [#135](https://github.com/renjianguojinqianfan/langgraph-agent/issues/135)〕 | 删 `backend/headless.py`、`scripts/live_e2e.py`、`scripts/live_skill_test.py`、`backend/services/run_manifest.py`；删 `ci.yml` 的 `live-e2e` job 与两步 `--check`；删 `config.py` 的 `run_manifest_*` | 离线全绿（对应测试显式重裁并写明理由）；CI 步骤数变少；前端不受影响 |
| **S2 新增钩子拦截**〔已合 [#144](https://github.com/renjianguojinqianfan/langgraph-agent/pull/144)；票面 [#136](https://github.com/renjianguojinqianfan/langgraph-agent/issues/136)〕 | 新增 `backend/core/agent/policy.py`（读设置里的规则表）；接到 `nodes.py::executor` 里今天算 `need_confirm` 的那一处；命中 → 不执行 + `{block, reason}` 当结果回模型 | 新增离线测试（命中即拒 / 不命中照旧）；既有测试全绿——**本片是纯增量** |
| **S3 删验证层**〔已合 [#143](https://github.com/renjianguojinqianfan/langgraph-agent/pull/143)；票面 [#137](https://github.com/renjianguojinqianfan/langgraph-agent/issues/137)〕 | `grader.py`、`reflect` 节点及其 `_verify_completion` / `_expectation_failures`、state 的 `_verification` / `_verify_attempts` / `_rubric_attempts` / `expectations` / `rubric`、`Task` 的四个字段、`Expectation` / `TaskReview`、`POST /tasks/{id}/review`、`accept_unverified`、`UNVERIFIED` 终态、`verification` / `task_unverified` / `task_accepted` 三个 SSE 事件、grader 对 `aux_llm` 的消费（配置本身留给摘要 / 风险语义）；图改「说完 → 直接 finish」 | 离线全绿；无回环；`check:sse` 四处对齐仍绿 |
| **S4 删闸门 + 风险扫描**〔已交 [#149](https://github.com/renjianguojinqianfan/langgraph-agent/pull/149)；票面 [#138](https://github.com/renjianguojinqianfan/langgraph-agent/issues/138)〕 | `risk.py`、`risk_scan` 节点、`_plan_confirm` / `_mark_high_steps_skipped`、`human_confirm_node` / `_ask_human` / `_confirm_skip_text` / `_needs_confirm` 重算块、state 三个确认字段、`graph.py` 两个节点与边、`task_manager` 的 `request_confirm` / `consume_confirm` / `confirm` / `_raise_if_parked_on_gate`、`auto_approve`（`TaskManager` 的 `_auto_approve` 构造参数与 `AgentRuntime` 的用法——S1 后已无生产消费者，只剩测试用）、`/confirm` 端点、`ConfirmRequest`、`confirm_outcome`；前端 `ConfirmDialog` / `confirmSlot` / `confirmOutcome` / `check:confirm` / 五档终态 / `RiskBanner`。**保留** `_sweep_dangling_tool_calls`（它补的是「中断在半轮」那个洞） | 离线全绿；危险动作靠 S2 的钩子；前端没有假按钮（词汇表绿） |
| **S5 一条 loop + 两模式**〔[#139](https://github.com/renjianguojinqianfan/langgraph-agent/issues/139)〕 | `planner` / `executor` 合并为一个 `agent` 节点；prompt 与工具面按模式选；任务上加模式开关（默认执行，黏性）；一轮没有工具调用即停下；前端：对话流 + 计划区（可改）+ 输入框 + 「重新规划」 | 离线测试（切模式换工具面 / 一轮结束即停下 / 停下能续 / 步数上限仍在 / 计划可改随后生效）+ **开服务在网页端跑一遍** |
| **S6 文档收尾**〔[#140](https://github.com/renjianguojinqianfan/langgraph-agent/issues/140)〕 | `README.md` 身份段重写、`GLOSSARY.md` 词汇、`AGENTS.md` 路由表与「验证入口」、`.env.example` 清理；并收口两份与已退役评测面绑定的规划文档：`docs/roadmap-pawbench.md`、`docs/pawbench-harness-interface.md`（后者含指向 `../scripts/live_e2e.py` 的死链）；另收 S3/S4 落在活文档里的残余节点/概念名——`docs/architecture.md` §2/§4 的 `reflect`，`NOTES.md` / `MISSION.md` / `lessons/*.html` / `reference/*.html` 里同一批旧拓扑名（`reflect` / `subagent_split`），以及 `docs/grader-fn-trial-2026-10-13.md`（#137 删掉的 grader 试测记录，归档或退役二选一） | 文档里不再出现已删概念（`UNVERIFIED` / `reflect` / `grader` / 确认闸门 / 风险扫描 / headless / run_manifest）；非归档文档里没有指向已删文件的死链。**归档例外按 #135 AC2 的口径**：`OVERVIEW.md` / `docs/incremental-*` / `docs/migration-*` / `.agents/retros/*` / `docs/adr/*` / `docs/specs/*` / 四份带日期的调研快照（`agent-benchmark-survey` / `agent-comparison-report` / `capability-first-principles` / `DEEP_RESEARCH_*`） |

## 纪律

- **删测试要显式重裁**：S1 / S3 / S4 都要删测试，与「测试只增不减」冲突。按 #132 先例，
  每个删除在本片里写明「删的是哪个已删功能的测试」，不许静默消失。
- **每片合前手跑一次真模型**：CI 不再跑真模型，所以这是唯一的真模型信号。不用每片都跑满两场景，
  但 S3 / S4 / S5 一定要跑。
- **`AGENTS.md` 的局部改动随片走**（它是指挥棒），S6 只做全局一致性收尾。

## 本方案按此前默认记的四条

1. **计划可编辑**：停下时可改，改完随下一条消息生效（需要一个 `PUT /tasks/{id}/plan`）。
2. **「重新规划」= 输入区一个小按钮**（不用打字让系统猜）。
3. **回滚（snapshots）保留**：不在退役清单里，执行模式「写乱了退回去」用得上。
4. **不做「停的原因」字段**：三种停法在界面上都是同一个「停下」，那一层省掉。

## 附

- **跟进票**：`pyproject.toml` 的 UP 规则族重估（冻结区解除后其理由已失效）→ [#141](https://github.com/renjianguojinqianfan/langgraph-agent/issues/141)。
- 一次性原型：`prototype/plan-build-modes.html`（单文件、未跟踪）。S5 已按新形态落地，结论已并入本文与
  ADR 0008；原型的「任务开头自动 plan」形态已过时，不再维护——作为一次性分支留档或丢弃，不入库。
