# P0-B 完成验证（reflect 落地自检）实现规范

> 🗄️ **2026-10-19 退役提示**：本文描述的完成验证栈已随 [ADR 0008](../adr/0008-single-loop-plan-build-modes.md)
> 整体退役（`reflect` 节点在 S3 / #137 删除）。**正文一字不动，保留为决策留痕**；现役形态见 README「核心模型」。

> 生成于 2026-09-15，经 `/grilling` 两轮收敛（Round 1 Q1–Q7 + Round 2 Q8–Q13，用户「全按推荐」）。
> 上游依据：`docs/capability-first-principles.md`（P7 / §六 P0-B）、`docs/roadmap-pawbench.md`（§三 能力清单、M2 前置）。
> 本文件是 P0-B 的权威设计源。**2026-09-15 经代码实证 review 修订**：补 error 路径守卫、headless 结果暴露、S2 三重收窄（详见 §三 / §4.2 / §4.5）。关联 Issue：#25（初版落地）→ **#46（2026-10-11 第一步修订）**：终态换 `UNVERIFIED`、`degraded` 布尔换 `verification.reason`、新增调用方声明的 `expectation`、循环入口清扫悬空 `tool_call`。**Q4 的安全原则（绝不因验证误判判 FAILED）不变，改的是终态命名与机器边界**——见 [ADR 0005](../adr/0005-terminal-status-unverified.md) / [ADR 0006](../adr/0006-expectations-declared-by-caller.md)，本文各节「#46 修订」标注以那两份为准。**#46（2026-10-13 第二步）**：Q1 里「留 P1」的 **B（`aux_llm` 独立裁判）已落地**，故 Q5 那句「零 LLM 成本」不再无条件成立（只有声明了 `rubric` 的任务才花 aux token，且确定性判据先行、有缺口一次都不叫裁判）；§三 与 §八 里「语义级内容打分仍是 #46 第二步」这类**现在时**表述自本日起作废。决策与残留风险见 [ADR 0007](../adr/0007-grader-error-not-fatal.md)，开工前置的假阴性率实测见 [grader 试测记录](../grader-fn-trial-2026-10-13.md)。**第二步的切片 B（同日）**再补两处：**Q3 的一本账变两本**（`verify_rubric_max_retries`，代价是最坏回环 2→4 轮）与 §八 划给 P1 的**人工复核出口已收编**（`POST /tasks/{id}/review`，旁挂不改写机器判决）。本文各节标「#46 切片 B」的修订以这两处为准。

## 一、动机与目标

- **第一性原理 P7**：模型「我做完了」的声明本身不可信 → 完成验证（self-verification）。
- **现状病灶**：`reflect` 是空壳（`nodes.py:565` 仅 `return state`）；executor 无 tool_call 时直接写 `final_answer` + `_last_action="final_answer"`（`nodes.py:438-445`）；`_after_reflect` 见 `final_answer` **无条件 → finish**（`graph.py:141-142`）。即「模型说完成就完成」。
- **业界印证**：编码 agent 通行「测试通过=完成，而非代码写完=完成」；Claude Code `/goal` 用**独立小模型**判达成；LangGraph 自纠错环 `repair(max 2)` + 「测试输出必须进状态」。核心教训：**验证者必须独立于声明者，而最强的独立是「一段不会被说服的确定性代码」**（`os.path.exists()` 不撒谎），而非让主模型自评（「结构性幻觉」）。
- **PawBench 口径**：self-verification 是原子能力；`grade()` 做产物级硬校验（文件落盘/diff/exit code）；「虚假完工」是明示掉分点。
- **目标**：模型声称完成时，reflect 用**确定性代码**复检可验证的交付物；不达标则**带证据回环**继续，而非直接 finish。

## 二、设计决策（grilling 定案）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 验证机制 | **A 纯确定性**为内核；B（aux_llm 独立裁判）/ D（结构化验收标准）留 P1 |
| Q2 | 失败回环 | 复用现有 `reflect→planner` 边 + 注入反馈消息（**不改拓扑**）|
| Q3 | 回环预算 | 新增 `verify_max_retries`（默认 2）独立小闸 + `max_steps` 总预算兜底。**#46 切片 B 修订：两本账**——确定性层的缺口烧 `verify_max_retries`，裁判层的 `needs_revision` 烧 `verify_rubric_max_retries`（默认也是 2），互不扣减；共享一本账会让「一次都没请到裁判」的机械缺口把裁判的轮数吃光。代价是**最坏回环总次数由 ≤2 变 ≤4**（两层各自 2 轮），总兜底仍是 `max_steps` |
| Q4 | 耗尽终态 | **#46 修订**：独立终态 **`UNVERIFIED`** + `verification.reason` 记原因（原来是「降级 COMPLETED + `degraded` 警告」）；**绝不判 FAILED 这条原则不变**（误判比现状更糟）|
| Q5 | 开关默认 | `verify_enabled` 默认 **true**（纯确定性、零 LLM 成本、只回环不判死）|
| Q6 | 可观测 | 发 `verification` 事件 + `_verification` 入 state + 落 trace |
| Q7 | 子任务图 | `mode="subtask"` **同接入**（逻辑一致，无确认闸门）|
| Q8 | 验证信号 | **S1 产物完整性 + S2 窄失败闸**（§三）；completeness 留 P1 |
| Q9 | 范围/放行 | 无 artifact 且无 failed tool_call → **no-op PASS**（不骚扰纯问答任务）|
| Q10 | 反馈内容 | 注入 system 消息，含**具体失败项** + 「勿重复声称完成」|
| Q11 | 实现落点 | 验证写在 **reflect 节点**，靠改 `_last_action`+`_verify_attempts` 驱动现有路由；**router 零改、受保护块不碰** |
| Q12 | 对外暴露 | `_verification` state 字段 + `verification` 事件 + **Task/API 加只读 `verification` 字段**；前端 UI 留后续 |
| Q13 | 配置字段 | `verify_enabled`(true) + `verify_max_retries`(2) 进 Settings；同步 .env.example/README/AGENTS |

## 三、验证判据（确定性，S1 + 窄 S2）

reflect 在 `_last_action == "final_answer"` 且 `verify_enabled` 且未 stop 时执行：

- **E 声明式 expectation（#46 新增，排在最前）**：调用方提交任务时声明的可机械判定条件——`artifact:<沙箱根相对路径>`（存在且非空）、`tool_success:<工具名 | *>`（至少一次成功调用）。判据来自**声明方**而非模型自报（ADR 0006）；**没声明 = 信息型任务 → 下面的 S1/S2/no-op 一字不变**。判不动的形态算未通过（fail-closed）。
- **S1 产物完整性**：枚举本次 run 已登记的 artifacts（经 **tm 权威侧**读取，守 P3 副本语义、不依赖 state 副本），逐个复检 `Path(art.path).exists()` 且 `stat().st_size > 0`。任一不满足 → 记一条失败项。
- **S2 窄失败闸（三重收窄）**：`成功产物数 == 0` **且** `成功 tool_call 数 == 0` **且** steps 里存在 `tool_call.status == "failed"` → 记失败。语义 =「**全程所有尝试都失败却声称完成**」。理由（review 定案）：只要有 ≥1 成功产物**或** ≥1 成功工具调用就不打回——彻底消除「纯问答任务中途一次 web_search 失败、模型凭知识作答」的误判面，且无需维护任何工具读写分类清单。
- **no-op PASS**（Q9）：`无 artifact 且无 failed tool_call` → 直接通过（纯问答/推理任务不被骚扰）。
- **判定**：`failures = E失败项 ∪ S1失败项 ∪ S2失败项`；`passed = (failures 为空)`；`passed=false` 且声称完成 → 终态 `UNVERIFIED`（不写 COMPLETED）。

**明确不做（P0-B 边界）**：completeness（「该产出却没产出」）——#46 第一步已把它**接住**，但只接住**调用方声明过**的那部分（E），没有声明就仍无从得知任务本来欠什么。语义级内容打分（LLM 裁判）已于 **#46 第二步**（2026-10-13）落地，见 [ADR 0007](../adr/0007-grader-error-not-fatal.md)。

## 四、实现方案

### 4.1 状态字段（`state.py` AgentState 追加）
- `_verification: Dict[str, Any]` — `{passed: bool, failures: List[str], attempts: int, reason: str}`（**#46**：`reason` ∈ `"" | pending | retry_exhausted | rubric_exhausted | max_steps | grader_error`，取代旧的 `degraded` 布尔；**#132 返工 C**：两本账烧完分名，语义那本落 `rubric_exhausted`）
- `expectations: List[Dict[str, str]]` — **#46 新增**：`[{type: artifact|tool_success, target}]`，由调用方在提交时给；落进持久化 `Task` 所以 resume 后仍按同一条线判
- `_verify_attempts: int` — 验证失败回环计数（独立于 `step_index`）
- 更新 `_last_action` 注释取值集：追加 `verify_failed`（`_last_action` 是 `str` 非 Literal，仅改注释，无类型面变更）

### 4.2 reflect 节点（`nodes.py`，替换空壳；不碰 548-562 受保护块）
```
def reflect(self, state):
    if self._stopped(state): return state
    if state.get("error"): return state                   # 错误路径不验证（executor LLM 异常也写 final_answer，nodes.py:368-370）；finish 自判 FAILED
    settings = getattr(self.tm, "settings", None) or get_settings()
    if not getattr(settings, "verify_enabled", True): return state   # 关闸 = 原空壳（零回归）；getattr 兜底 SimpleNamespace 假 settings
    if state.get("_last_action") != "final_answer": return state   # 只在声称完成时验
    failures = self._verify_completion(state)             # E + S1 + S2，纯确定性
    attempts = state.get("_verify_attempts", 0)
    if not failures:                                      # 通过
        state["_verification"] = {passed:True, failures:[], attempts, reason:""}
        self._publish("verification", {...})
        return state                                      # _last_action 仍 final_answer → finish
    if attempts >= settings.verify_max_retries:           # Q4 甲（#46 改终态命名）：不判死，落 UNVERIFIED
        state["_verification"] = {passed:False, failures, attempts, reason:"retry_exhausted"}
        self._publish("verification", {... reason ...})
        return state                                      # 保持 final_answer → finish(<UNVERIFIED>)
    # 回环：改 _last_action，现有 _after_reflect 自然落 return "planner"
    state["_verify_attempts"] = attempts + 1
    state["_verification"] = {passed:False, failures, attempts:attempts+1, reason:"pending"}
    state.setdefault("messages", []).append({"role":"system","content": <Q10 反馈+具体失败项>})
    self._publish("verification", {... failed, loop back ...})
    state["_last_action"] = "verify_failed"
    return state
```
- 新增私有方法 `_verify_completion(state) -> List[str]`：做 E+S1+S2，返回失败项文案列表。expectation 的 `artifact` 路径经 `settings.artifacts_path`（沙箱根）解析、越出沙箱根即判失败（#46）；artifact 经 **tm 权威侧**读（`getattr(self.tm, "_active_states", {})` 兜底 SimpleNamespace 假 tm，守 P3 副本语义）；tool 状态经 `state["steps"][*]["tool_calls"][*]["status"]`（`"failed"`/`"success"` 字面量已在 nodes.py:481 落定）。
- **error 路径守卫**（review 漏洞1）：`state.get("error")` 已置时 reflect 直接返回、不验证——executor LLM 异常分支同样写 `final_answer`+`_last_action="final_answer"`（nodes.py:368-370），不守卫会把出错任务拖进验证回环、改变错误语义并扰动 MockLLM 脚本化异常的存量测试。
- **#46 切片 B（2026-10-13）修订上面这段伪代码的三处**（伪代码按原样留着，好看出改了哪）：
  ① `failures` 不再是「E+S1+S2 一把算」——**确定性层先跑且独自成立**，它有缺口时一次都不叫裁判（机械可判的缺口不花 aux token，模型拿到的是具体缺口而不是第二意见）；只有它空手时才请 `aux_llm` 裁判评 `rubric`。
  ② 预算是一本变两本：`_verify_attempts`（确定性）与 `_rubric_attempts`（裁判的 `needs_revision`）各自与自己的闸比。
  ③ 多一条不进任何账的短路：`grader_error`（裁判缺席 / 抛错 / 输出不可解析 / rubric 本身没法评）直接落 `UNVERIFIED(reason="grader_error")`，不回环、不烧预算、更不判 `FAILED`——见 [ADR 0007](../adr/0007-grader-error-not-fatal.md)。

### 4.3 路由（`graph.py`）
- **`_after_reflect` 零改**：reflect 把 `_last_action` 改成 `"verify_failed"`（非 `final_answer`）→ 现有逻辑落 `if step_index >= max_steps: finish` 否则 `return "planner"`；`max_steps` 兜底天然生效。
- **边界（#46 修订）**：若 `max_steps` 先于 `verify_max_retries` 触发（罕见，2≪15），回环中的 `_verification.reason` 还是 `pending`——`_finalize_terminal` 兜底把它改写成 `reason="max_steps"`，终态 `UNVERIFIED`。原来的「补 `degraded=true`」说法作废。
- **#46 切片 B 补**：这本兜底账现在有**两处**，不是一处。主任务侧在 `_finalize_terminal`，子任务侧在 `SubAgentExecutor.run_subtask` 的折叠里（`subagent.py`）——子任务不落 `Task` 记录、不经 `_finalize_terminal`，少写一处就把 `pending` 原样递给了父模型，而 `pending` 的字面意思是「还在验」，实际是「没轮次了」。两侧各有一条测试钉着（`test_finalize_unverified_publishes_event_and_max_steps_reason` / `test_subtask_cut_by_the_step_budget_reports_the_budget`）。

### 4.4 配置（`config.py` + `.env.example` + docs）
- `verify_enabled: bool = True`、`verify_max_retries: int = 2`
- **#46 切片 B 加**：`verify_rubric_max_retries: int = 2`（裁判层 `needs_revision` 的独立小闸，与 `verify_max_retries` 互不扣减；见 Q3 的「两本账」）。`.env.example` 同段跟随，README 配置段同步。
- Settings 前缀注释组 15 → 16；`.env.example` 加「── Verification (P0-B) ──」段；README + AGENTS §3 配置段同步。

### 4.5 对外暴露（Q12）
- `_finalize_terminal`（`task_manager.py`）把 `final.get("_verification")` 映射到 Task。
- `api/schemas.py` Task 加只读 `verification: Optional[Dict[str, Any]] = None`（additive，不破坏现有响应信封）。
- **`headless.py` `_collect` + `_RESULT_KEYS` 加 `verification` 键**（review 漏洞2）：PawBench 评测台吃 headless JSON，不补则 Q4 的软失败统计在评测侧拿不到数据；`test_headless.py` 的断言是 `key in result`（非精确键集），加键安全。**#46 修订退出码段**：`0/1/2/3` 语义不变，新增 **`4 = UNVERIFIED`**（`_TERMINAL` 同时加该值，否则等待循环认不出终态）；合格线经 `--expect kind:target` 声明（可重复），REST 侧是 `CreateTaskRequest.expectations`。
- `verification` 事件经 event_bus → SSE → trace（自动落 JSONL）。
- 前端 UI 展示：P0-B 只落 API/trace；**#46 起补上终态可见**——`UNVERIFIED` 徽章（`HistoryPanel` / `TaskHeader`）+ `taskStore` 认 `task_unverified` / `verification` 事件，失败项文本在详情头可见（否则新终态在 UI 上等于没说）。

## 五、测试计划（TDD，先红后绿；全离线 MockLLM + tmp_path）

新增 `backend/tests/test_verify.py`：
1. **S1 空产物**：file_io 写空文件 → 登记但 size==0 → reflect 判失败回环（`_last_action=verify_failed`）→ 第二次写非空 → COMPLETED，`_verification.passed=True`。
2. **S1 产物消失**：登记后删文件 → `exists()==False` → 失败回环。
3. **S2 全程失败 + 收窄反例**：零成功产物 + 零成功工具 + ≥1 failed + final_answer → 判失败回环；**反例**：1 个成功工具（web_search）+ 1 个 failed + 零产物（纯问答）→ **PASS 不打回**。
4. **Q9 no-op**：纯 final_answer、无 artifact 无 failed tool → 直接 PASS（attempts=0，不回环）。
5. **Q3/Q4 预算耗尽（#46 重裁）**：持续失败 → attempts 达 `verify_max_retries` → 终态 **`UNVERIFIED` + `reason="retry_exhausted"`**，**不 FAILED**（原用例名 `test_degrade_after_max_retries` 已改 `test_unverified_after_max_retries`，并把旧版漏掉的那一半补上：reflect 的输出要喂 `finish` 断终态，而不只断 `_last_action`）。**#132 返工 C 补**：语义那本账（`verify_rubric_max_retries`）烧完 → **`UNVERIFIED` + `reason="rubric_exhausted"`**，与机械账分名（`test_rubric_exhausted_after_max_retries` 钉节点 seam，`test_rubric_exhausted_exits_unverified` 钉 headless 的 `exit 4` + JSON 结果）。
6. **Q5 关闸零回归**：`verify_enabled=false` → reflect 等价原空壳（final_answer 直接 finish）。
7. **Q2/Q10 反馈注入**：失败回环后 messages 含带具体失败项的反馈消息。
8. **Q7 子任务图**：`mode="subtask"` 同样验证。
9. **Q12 暴露**：Task.verification 回填；verification 事件发出。
10. **error 路径守卫**（review 漏洞1）：executor LLM 异常 → `state["error"]` 置 + final_answer → reflect **跳过验证**、不注入反馈、不回环，finish 判 FAILED（错误语义不变、不扰动存量异常测试）。
11. **headless verification 键**（review 漏洞2）：`_collect` 结果含 `verification`、`_RESULT_KEYS` 同步；未通过验收的场景读得出 `reason`（#46 起字段是 `reason`，`degraded` 已退役）。
- 守 conftest 隔离；不碰 checkpoint/resume；受保护块零改动。

## 六、纪律与约束

- **受保护文件零改动**：`resilience.py`/`registry.py`/`nodes.py` 的 `_needs_confirm` 重算块（548-562）**一字不动**；本改只动 reflect 节点体（空壳）+ 新增私有方法 + 新字段。
- **resume 契约零改动**：不碰 checkpoint/durability/reject 语义。
- **conftest 隔离不破坏**；无 `# type: ignore`/`# noqa`；无新增 mypy override。
- **getattr 鸭子兼容**（P3 既定规矩）：reflect 读 tm/settings 新属性一律 `getattr` 兜底（`test_qa_nodes`/`test_p2_mcp` 用 `SimpleNamespace` 假 tm/settings，无 `_active_states`/`verify_enabled`）。
- **conftest 策略**：先**不**加隔离开关、让 `verify_enabled` 默认 ON 跑全量 433（Q5「零回归」的唯一真实证据）；若出现**语义性**破坏（非「verification 字段新增」这类），再回落 checkpoint 同款（conftest `setdefault VERIFY_ENABLED=false` + `test_verify` opt-in）。
- 改到 AGENTS.md 时顺手把测试数快照 426 → 实测值。
- **门禁**：`ruff` + `mypy` 0 错；全量 `pytest` 绿（当前 433 + 新增）。
- **分支**：`feat/reflect-verification`（roadmap §五 指定，review 最严、单独走）；PR + CI 全绿才合。
- **TDD**：先写测试再实现。

## 七、验收标准

- [ ] `verify_enabled=true` 时，空产物 / 产物消失 / 零产出+失败工具 三类虚假完工被拦截并回环纠正。
- [ ] 无交付物任务 no-op 放行、不误判。
- [ ] 预算耗尽 → `UNVERIFIED` + `reason`（#46），绝不因验证误判 FAILED。
- [ ] `verify_enabled=false` 完全等价原空壳行为（零回归）。
- [ ] `_verification` 落 state/事件/trace/Task；API 只读字段可查。
- [ ] error 路径（`state["error"]` 置）跳过验证、语义不变、不扰动存量异常测试。
- [ ] S2 收窄：纯问答 + flaky 搜索失败 + 有成功工具 → 不误判打回。
- [ ] headless JSON 结果含 `verification` 键（评测台可读 `reason` 与失败项），未通过验收落 `exit 4`（#46）。
- [ ] 受保护块 / resume 契约 / conftest 隔离零改动；ruff/mypy 净；全量 pytest 绿。
- [ ] 子任务图同接入。

## 八、明确不做（划到 P1）→ **#46 部分收编**
- completeness 验证（声明式预期产物 / 结构化验收标准 = Q1 D）：**第一步已落**，但只覆盖**调用方声明过**的完成条件（`expectation`，[ADR 0006](../adr/0006-expectations-declared-by-caller.md)）——没声明的任务仍然问不出「本来欠什么」，这是有意保留的边界不是漏洞。
- LLM 裁判层（Q1 B，aux_llm **独立**判者——绝不让主模型自评）：#46 **第二步已于 2026-10-13 落地**。
  当时写下的开工前置是先量假阴性率、不可接受就缩小判据而不是把终态改回 COMPLETED——前置结果
  是假阴性 **0/24 与 0/23**（两口径都是 0），所以判据未缩；真风险在反方向（裁判偏松），
  处理见 [试测记录](../grader-fn-trial-2026-10-13.md) 与 ADR 0007 第 5 条。
- 人工复核出口（把 `UNVERIFIED` 接受为 `COMPLETED`）：**#46 切片 B 已于 2026-10-13 落地**——`POST /api/tasks/{id}/review {reviewer, note}`。ADR 0005 那条重开条件附的「人工接受**不算**验证通过」写得比「翻终态」更硬，所以实现走的是**旁挂**：`Task.review` 与 `verification` 平级，`passed` 与原判因一字不动，被推翻的那一档记进 `overruled_reason` 让它可计数；改写 `passed` 就是换顶帽子的橡皮图章。headless 刻意无等价 flag（评测态房间里没有人，`exit 4` 就是它的诚实判决）。
- 前端 UI 的 verification 展示：#46 起已补终态徽章与失败项（见 §4.5）。
