# P0-B 完成验证（reflect 落地自检）实现规范

> 生成于 2026-09-15，经 `/grilling` 两轮收敛（Round 1 Q1–Q7 + Round 2 Q8–Q13，用户「全按推荐」）。
> 上游依据：`docs/capability-first-principles.md`（P7 / §六 P0-B）、`docs/roadmap-pawbench.md`（§三 能力清单、M2 前置）。
> 本文件是 P0-B 的权威设计源。**2026-09-15 经代码实证 review 修订**：补 error 路径守卫、headless 结果暴露、S2 三重收窄（详见 §三 / §4.2 / §4.5）。关联 Issue：#25（初版落地）→ **#46（2026-10-11 第一步修订）**：终态换 `UNVERIFIED`、`degraded` 布尔换 `verification.reason`、新增调用方声明的 `expectation`、循环入口清扫悬空 `tool_call`。**Q4 的安全原则（绝不因验证误判判 FAILED）不变，改的是终态命名与机器边界**——见 [ADR 0005](../adr/0005-terminal-status-unverified.md) / [ADR 0006](../adr/0006-expectations-declared-by-caller.md)，本文各节「#46 修订」标注以那两份为准。

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
| Q3 | 回环预算 | 新增 `verify_max_retries`（默认 2）独立小闸 + `max_steps` 总预算兜底 |
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

**明确不做（P0-B 边界）**：completeness（「该产出却没产出」）——#46 第一步已把它**接住**，但只接住**调用方声明过**的那部分（E），没有声明就仍无从得知任务本来欠什么。语义级内容打分（LLM 裁判）仍是 #46 第二步。

## 四、实现方案

### 4.1 状态字段（`state.py` AgentState 追加）
- `_verification: Dict[str, Any]` — `{passed: bool, failures: List[str], attempts: int, reason: str}`（**#46**：`reason` ∈ `"" | pending | retry_exhausted | max_steps | grader_error`，取代旧的 `degraded` 布尔）
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

### 4.3 路由（`graph.py`）
- **`_after_reflect` 零改**：reflect 把 `_last_action` 改成 `"verify_failed"`（非 `final_answer`）→ 现有逻辑落 `if step_index >= max_steps: finish` 否则 `return "planner"`；`max_steps` 兜底天然生效。
- **边界（#46 修订）**：若 `max_steps` 先于 `verify_max_retries` 触发（罕见，2≪15），回环中的 `_verification.reason` 还是 `pending`——`_finalize_terminal` 兜底把它改写成 `reason="max_steps"`，终态 `UNVERIFIED`。原来的「补 `degraded=true`」说法作废。

### 4.4 配置（`config.py` + `.env.example` + docs）
- `verify_enabled: bool = True`、`verify_max_retries: int = 2`
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
5. **Q3/Q4 预算耗尽（#46 重裁）**：持续失败 → attempts 达 `verify_max_retries` → 终态 **`UNVERIFIED` + `reason="retry_exhausted"`**，**不 FAILED**（原用例名 `test_degrade_after_max_retries` 已改 `test_unverified_after_max_retries`，并把旧版漏掉的那一半补上：reflect 的输出要喂 `finish` 断终态，而不只断 `_last_action`）。
6. **Q5 关闸零回归**：`verify_enabled=false` → reflect 等价原空壳（final_answer 直接 finish）。
7. **Q2/Q10 反馈注入**：失败回环后 messages 含带具体失败项的反馈消息。
8. **Q7 子任务图**：`mode="subtask"` 同样验证。
9. **Q12 暴露**：Task.verification 回填；verification 事件发出。
10. **error 路径守卫**（review 漏洞1）：executor LLM 异常 → `state["error"]` 置 + final_answer → reflect **跳过验证**、不注入反馈、不回环，finish 判 FAILED（错误语义不变、不扰动存量异常测试）。
11. **headless verification 键**（review 漏洞2）：`_collect` 结果含 `verification`、`_RESULT_KEYS` 同步；degraded 场景可读。
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
- LLM 裁判层（Q1 B，aux_llm **独立**判者——绝不让主模型自评）：#46 **第二步**，开工前置是先量假阴性率（见票面），不可接受就缩小判据、而不是把终态改回 COMPLETED。
- 人工复核出口（把 `UNVERIFIED` 接受为 `COMPLETED`）：仍未做，是 [ADR 0005](../adr/0005-terminal-status-unverified.md) 的重开条件。
- 前端 UI 的 verification 展示：#46 起已补终态徽章与失败项（见 §4.5）。
