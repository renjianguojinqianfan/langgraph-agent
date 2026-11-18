# 326 S0：现状基线与旧断言迁移清单

> 2026-11-18 由 S0（[#327](https://github.com/renjianguojinqianfan/langgraph-agent/issues/327)）产出。
> 权威输入：[#326](https://github.com/renjianguojinqianfan/langgraph-agent/issues/326)、
> [ADR 0013](../adr/0013-kernel-simplification.md)、
> [实施计划](qianfan-runtime-simplification-plan.md) §5。
> 本文只回答两件事：改实现前哪些现状测试必须绿；哪些旧断言会按新合同退役或改写、真实覆盖迁到哪。
> 不复制票面的接口细节，也不把「删测试」当迁移。

## 1. 现状基线（未改实现前必须绿）

四块测试复用**现有套件**，不新增第二套断言。本次只补一处覆盖缺口：
`backend/tests/test_api.py::test_one_tool_round_emits_the_expected_event_sequence` 固定整轮事件序
（此前只有 agent+tool 子图序与事件类型集合断言）。2026-11-18 在 S0 工作树（未改实现）实跑通过；
后续切片改实现前先跑同一组文件，红了先判断是实现变化还是环境假红。

| 块 | 测试文件 | 主要 seam | 固定住的现状 |
|---|---|---|---|
| 新建 / 恢复 / 续说 | `backend/tests/test_api.py`、`test_resume.py`、`test_s5_agent_modes.py`、`test_checkpointer.py`、`test_lifecycle_failures.py`、`test_orphan_reconcile.py`、`test_workspace.py` | 任务 API + TaskManager + Persistence/checkpoint | 创建信封、默认目录、停下后 resume（仅 INTERRUPTED）、续说（INTERRUPTED/FAILED）、停止→重建→恢复、进程重启后续说、终态无悬空步骤、孤儿对账 |
| 模型选择 | `backend/tests/test_models.py`、`test_providers_api.py`、`test_llm.py` | ModelStore + Provider/模型 API + `LLMClient.complete` | 默认/显式模型、任务中途换模型下一条消息生效、子代理与压缩跟随父轮模型、删除模型后不偷换 Provider、Key 不回显、双协议请求映射 |
| 工具面 | `backend/tests/test_c1_minimal_face.py`、`test_packages.py`、`test_plugins_face.py`、`test_plugins_store.py`、`test_plugins_api.py`、`test_tools.py`、`test_bash_tool.py`、`test_subagent.py`（`test_s5_agent_modes.py` 的计划模式面共用） | TaskManager 装配 + 工具 dispatch + 子代理档位 | 计划模式只读面与档外拒绝、执行模式全量面、能力包零实例/零 schema/零 dispatch/零注入/不连 MCP、子代理档位、命令执行保障 |
| SSE 事件序列 | `backend/tests/test_eventbus.py`、`test_sse_delivery.py`、`test_sse_publish_ast.py`、`test_policy_block.py`、`test_api.py`、`test_trace.py` | EventBus → SSE / trace | 发布-订阅与回放、迟到订阅、有界缓冲、跨线程送达顺序、发布点与 `docs/architecture.md` §3.4 表对齐、一次 agent+tool 的确切事件序、trace 写入顺序与读取合同 |

复跑命令（根目录，解释器按 `AGENTS.md`）：

```bash
.venv314/Scripts/python.exe -m pytest \
  backend/tests/test_api.py backend/tests/test_resume.py backend/tests/test_s5_agent_modes.py \
  backend/tests/test_checkpointer.py backend/tests/test_lifecycle_failures.py \
  backend/tests/test_orphan_reconcile.py backend/tests/test_workspace.py \
  backend/tests/test_models.py backend/tests/test_providers_api.py backend/tests/test_llm.py \
  backend/tests/test_c1_minimal_face.py backend/tests/test_packages.py \
  backend/tests/test_plugins_face.py backend/tests/test_plugins_store.py backend/tests/test_plugins_api.py \
  backend/tests/test_tools.py backend/tests/test_bash_tool.py backend/tests/test_subagent.py \
  backend/tests/test_eventbus.py backend/tests/test_sse_delivery.py backend/tests/test_sse_publish_ast.py \
  backend/tests/test_policy_block.py backend/tests/test_trace.py \
  -q
```

受限 harness 里若临时根被拒（`PermissionError`）或 bash 初始化假红，按
[`docs/agents/verification-notes.md`](../agents/verification-notes.md) 换仓库外解释器或回自己终端；
不改产品代码换绿。

## 2. 旧断言 → 新合同 → 原因 → 迁移/退役去向

规则：**退役规格的断言可以撤销或改写，真实功能覆盖必须迁到新接口**；不得只删测试换绿色。
下表只列会被四项裁定影响到的断言；未列出的基线测试按原样保留。

### S1 模型解析与接口收口（#328）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_models.py::test_v1_models_json_migrates_to_providers_and_catalog` | 当前格式唯一；v1 `models.json` 明确拒绝且原文件字节不变 | v1 自动迁移是「新旧两套并存」的主要来源，且会静默改写用户文件 | 退役；S1 新增「v1 文件被拒、原文件不变」测试 |
| `test_models.py::test_v1_migration_is_idempotent_and_backs_up_once` | 同上，不保留迁移/备份重试链 | 幂等与备份只服务于迁移链本身，没有外部消费者 | 退役 |
| `test_models.py::test_existing_v1_backup_is_never_overwritten` | 同上 | 同上 | 退役 |
| `test_models.py::test_v1_default_falls_back_to_the_first_profile_when_unknown` | 默认模型只从当前 `models.json` v2 解析；未知 id 报错 | 「首个 profile 兜底」是旧投影的猜测语义 | 退役；未知/删除模型的明确失败由 `test_deleted_profile_makes_a_pinned_task_fail_loudly` 与 S1 新拒绝测试覆盖 |
| `test_models.py::test_migrated_profile_ids_still_pin_tasks` | 不做 id 迁移；任务上的模型 id 必须是当前存在的条目 | 迁移后的 id 兼容是迁移链的一部分 | 退役；当前格式的 id 固定与删除模型失败断言保留 |
| `test_models.py::test_v1_backup_is_a_byte_for_byte_copy` | 旧文件不读不写，拒绝即原文不变 | 字节级备份只对迁移有意义 | 退役 |
| `test_models.py::test_legacy_projection_carries_effective_sampling_params` | 运行解析返回明确的有效参数；不再维护 `/api/models` 扁平投影 | 旧投影与 Provider/ModelEntry 是两条读路径，参数继承应只有一处权威 | 退役；参数继承覆盖保留在 `test_entry_sampling_params_override_provider_defaults`、`test_sampling_params_are_validated_without_mutating`、`test_sampling_params_can_be_cleared_back_to_the_fallback` |
| `test_models.py::test_models_api_never_returns_the_key`、`test_model_profile_update_and_delete_endpoints`、`test_models_api_rejects_an_empty_model` | Provider/模型目录 API 是唯一写面与读面；旧扁平 CRUD 删除 | 两条 CRUD 会漂移；前端旧投影无生产消费者 | 改写：同一断言迁到 `/api/providers` + 模型目录测试；「任何响应不回 Key」必须在新面重建 |
| `test_models.py` 的 profile → provider/model 改名测试（`test_crud_default_and_delete_guard`、`test_delete_revokes_the_pasted_key`、`test_deleted_profile_makes_a_pinned_task_fail_loudly`、`test_deleting_the_rounds_profile_marks_the_task_failed`、`test_profile_edit_and_key_change_replace_the_cached_client`、`test_profile_edit_cannot_reinsert_the_old_cached_client`、`test_profile_validation_rejects_bad_urls_and_env_names`、`test_credentials_write_failure_is_readable_and_keeps_the_profile`） | 同一行为换到 Provider / 模型目录命名与接口 | 旧 profile 词表退役，断言语义保留 | 改名/改写为对应 provider/catalog 测试；不删除覆盖 |
| `test_providers_api.py::test_legacy_models_projection_and_provider_scoped_key` | Provider Key 由同一 Provider 下条目共享；旧扁平投影删除 | 旧投影是第二读路径 | 改写为 `test_provider_key_is_shared_by_its_entries` |
| `test_models.py::test_unknown_model_on_task_create_is_rejected` | 保持：任务创建使用未知模型 id → 4xx | 新合同要求明确拒绝，不是兼容 | 保留（可随新解析路径调整错误文案） |

### S2 任务与 checkpoint 当前合同（#329）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_s5_agent_modes.py::test_completed_is_not_a_live_status` | `COMPLETED` 不是现役状态；旧记录不再映射为 `INTERRUPTED`，而是按旧格式明确拒绝 | 状态映射是第二条状态解释源 | 改写：保留 `COMPLETED` 不在 `TaskStatus` 的断言；把「旧记录映射」改为「旧格式拒绝且原文不变」 |
| `test_workspace.py::test_legacy_task_gets_the_old_sandbox_workspace` | 旧任务不再回填 `data/artifacts`；新建任务省略目录才由发起入口工作目录确定 | 旧记录回填与新建默认值是两件事 | 退役；S2 新增「新建省略目录取发起入口 cwd」与「旧 workspace 缺失被拒」两条 |
| `test_s5_agent_modes.py::test_finish_leaves_a_legacy_step_without_round_alone` | 当前记录的 step 一定有 `round`；不再维护 `round=0` 旧展示/收尾分支 | 旧轮次展示是双解释源 | 退役或改写为「当前格式 step 的收尾」；`test_finish_does_not_touch_a_previous_rounds_done_step` 等同轮语义测试保留 |
| `test_persistence.py::test_legacy_tool_call_input_is_normalized_on_read` | 当前格式的 tool call input 是对象；旧字符串输入不再归一化，旧记录拒绝 | 读时归一化会掩盖旧格式，且与「拒绝不改原文」冲突 | 退役；当前格式的 `test_dict_tool_call_input_is_unchanged` 保留 |
| `test_persistence.py::test_legacy_input_in_tasks_json_loads_and_dumps_as_object` | 同上 | 同上 | 退役；当前格式往返测试保留 |
| `test_checkpointer.py` / `test_dangling_tool_calls.py` 里的 `_is_subtask` fixture | 删除无消费者的 `_is_subtask` 状态键 | 只写不读的 checkpoint channel 形状 | 随 S2 删键同步 fixture（不是断言退役） |
| `test_checkpointer.py::test_legacy_store_is_refused_with_actionable_error`、`test_resume_over_legacy_store_is_refused_as_no_checkpoint`、`test_store_stamped_by_a_newer_build_is_refused`、`test_store_stamped_by_an_older_build_is_refused` | **保留**：旧/未知 checkpoint 明确拒绝 | 这正是新合同本身，不是旧兼容 | 原样保留；`test_empty_legacy_store_is_accepted` 是「空库无可腐蚀数据」的放行，不是格式兼容，S2 复核后如收紧再改 |

### S3 执行核心收敛（#330）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_llm.py::test_mock_planner_returns_plan_as_json` | Mock 按**模式标记**返回；空工具面的 build 模式返回最终回答，不返回计划 JSON | 「无工具 = 旧 planner」是已复现的旧假设冲突，会把空工具执行误判成计划 | 改写：plan/build × 空工具面两条用例（S3） |
| `test_llm.py::test_mock_stream_yields_single_completion` | 无运行消费者的 `stream()` 预留接口删除；保留 SSE | 「以后可能用」不是消费者，接口留着会继续扩大测试面 | 退役 |
| `test_llm.py::test_openai_compat_stream_collects_reasoning_deltas` | 同上；SSE 不受影响 | 同上 | 退役；`complete()` 的 reasoning 覆盖保留 |
| `test_s5_agent_modes.py` 三入口正常/停止/异常的事件与读盘断言 | **保留并加强**：共享执行核心后三入口必须同序列 | 这是 S3 的验收基线，不是旧规格 | 原样保留；S3 增补 join/互斥、trace attach、落盘次序 |

### S4 统一命令执行与能力装配（#331）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_bash_tool.py` 整文件（Shell 发现用例另列） | 一个 `shell` 工具（`shell="bash"` 走 Git Bash）；工作区、环境白名单、输出上限、超时、停止、Job/进程树、后台清理全部保留；结果含实际 Shell 与退出码 | 执行保障是现有能力，不能因换 Shell 丢掉；旧工具名 `bash` 不再存在 | 迁移：同一行为在 `shell` 工具上 1:1 保留；S4 另加 pwsh 矩阵（原生退出 0/1/7、cmdlet 错误、复合语句、中文/引号/空参数、npm.cmd） |
| `test_bash_tool.py::test_missing_bash_returns_a_readable_error`、`test_find_bash_*`（5 例） | Windows 默认 `pwsh` 7、Git Bash 显式；缺指定 Shell 报错，不静默退 PS5.1、不换 Shell 重跑 | Shell 发现从「只找 Git Bash」变成「按选择找 pwsh / Git Bash」 | 改写：`find_pwsh` / `find_git_bash` 与缺 Shell 错误；保留 `system32` 跳过等安全细节 |
| `test_file_tools.py::test_new_tools_sandbox_policy`（`requires_confirm is False`） | 删除 `requires_confirm` 惰性元数据；schema 与策略钩子不变 | 字段不再参与运行期裁决，留着会误导 | 改写：断言字段不存在 / schema 不含确认元数据；策略钩子覆盖仍在 `test_policy_block.py` |
| `test_qa_p1_subagent.py::test_spawn_subagent_tool_requires_confirm` | 同上 | 同上 | 退役 |
| `test_mcp_danger_annotations.py` 整文件 | 删除 `danger_signal` 分类；MCP 连接、调用、错误与权限面保留 | 确认闸门已随 #138 退役，分类没有判决消费者 | 退役；MCP 覆盖保留在 `test_p2_mcp.py`（连接/调用/错误/环境白名单） |
| `test_packages.py`、`test_plugins_face.py`、`test_plugins_store.py`、`test_plugins_api.py` 中的 `bash` 包 id / 面断言 | 包 id 与工具名均为 `shell`；旧 `data/plugins.json` 的 `bash` id fail-fast，原文件不变，不静默映射 | 一个能力只能有一个 id；静默映射会把旧配置藏起来 | 改写为 `shell`；新增旧 id 拒绝测试；零实例/零 schema/零 dispatch/零注入/不连 MCP 的断言原样保留 |
| `test_plugins_api.py::ALL_PACKAGE_IDS`、`test_plugins_store.py` 的包集合、`test_packages.py` 的预设成员 | 同上；三预设成员关系不变（`minimal` / `research` / `full`） | 同上 | 改写 |
| `test_c1_minimal_face.py` 的工具面名单（含 `bash`） | 现役面是 `shell`；已退役工具仍不得回潮 | 名单漂移会静默放过旧名 | 改写名单 |
| `test_subagent.py` 的 `bash` 档位/拒绝断言（含 `EXPLORE_TIER` / `EXECUTE_TIER`） | `shell` 在子代理面**结构性不可达**；档外拒绝仍点名被拒工具 | 换名不能顺带打开子代理执行档 | 改写为 `shell` 并保留「点名拒绝」断言 |
| `test_workspace.py` 的 `bash` 工具调用与 cwd 断言 | 工具名 `shell`；任务工作区仍是起点 | 工具名变化不改变工作区语义 | 改写工具名，cwd/注入断言保留 |
| `test_orphan_reconcile.py`、`test_llm.py`、`test_models.py` 里作为 fixture 的 `tool_name="bash"` | 现役工具名 `shell` | fixture 名不必驱动行为，但留着旧名会让「不存在 `bash` 工具」的检查失真 | 改名（不改断言语义） |
| `test_policy_block.py` | **保留**：策略钩子只认设置规则表，命中即拒、不改参数、不 terminate | 这是 ADR 0008 的现役不变量，S4 只改目标名示例，不改语义 | 原样保留；S4 如新增 `shell` 规则示例，另加不替换本文件 |

### S5 + S6 共享后台与终端薄客户端（#332）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_repl.py::test_repl_claims_mode_repl_and_releases_on_exit` | 单例锁归服务进程；终端不持锁 | 客户端持锁会让终端与 Web 互斥，正是要消除的形态 | 退役；锁归属测试迁到服务端/CLI |
| `test_repl.py::test_repl_refuses_when_a_web_backend_is_running` | 终端连接已运行的后台，不拒绝、不再起第二个运行时 | 共享后台 + 同一任务 | 退役；替换为「终端连接既有后台」测试 |
| `test_repl.py::test_repl_refuses_when_another_repl_is_running` | 多个终端客户端可同时连接，由后台准入裁决 | 客户端不再持有单例 | 退役 |
| `test_repl.py::test_web_launcher_refuses_a_running_repl_without_probing` | `qianfan` 发现健康实例即复用，不探测 REPL 锁 | 没有 REPL 锁可探测 | 退役 |
| `test_repl.py::test_repl_recovers_from_a_dead_lock_record`、`test_repl_reports_manual_cleanup_when_claim_races` | 残留锁恢复归服务启动路径；终端只报连接错误 | 锁生命周期不再属于客户端 | 退役/迁到服务端启动测试 |
| `test_repl.py::test_build_runtime_wires_the_same_objects`、`test_real_runtime_runs_two_mock_tasks_in_one_session`、`test_two_tasks_run_in_one_session_with_new`、`test_second_message_continues_the_same_task`、`test_repl_module_entry_starts_and_exits` | 终端通过 REST/SSE 使用后台，不直接 `build_runtime` | 客户端不访问 TaskManager/Persistence/EventBus | 改写：fake HTTP/SSE transport + 真实后台 e2e |
| `test_repl.py::test_rendered_event_types_are_the_small_face`、`test_render_event_*` | 终端仍只渲染小事件面，但事件来自 SSE | 渲染契约保留，传输改变 | 迁移（同一名单/渲染断言，输入改为 SSE 事件） |
| `test_repl.py::test_ctrl_c_*`、`test_eof_exits_cleanly`、`test_stop_*`、`test_terminal_event_finishes_the_round_even_before_status_settles`、`test_status_polling_is_the_fallback_when_events_are_missing`、`test_stop_errors_are_printed_not_raised`、`test_send_persistence_error_is_printed` | 交互行为保留；错误来源改为 HTTP/SSE（认证过期、服务不可达、409 准入） | 传输改变不等于行为可以退步 | 改写：保留用户可见断言，替换注入替身 |
| `test_cli.py::test_serves_loopback_on_the_requested_port_and_opens_the_browser`、`test_main_delegates_the_browser_open_to_the_ready_watcher`、`test_port_env_is_exported_before_the_server_imports_settings` | `qianfan` 确保独立后台（detach + 等就绪 + 开浏览器）；`qianfan serve` 前台入口 | 前台 `uvicorn.run` 不再是日常入口 | 改写：detach/ready/port/browser 行为保留，进程模型改变 |
| `test_cli.py` 其余端口/构建提示/`--no-browser`/help 断言 | **保留**（`qianfan serve` 与 ensure 共用同一校验） | 这些是启动校验，不是前台运行假设 | 保留或按子命令重排 |
| `test_server_lock.py` | **保留并扩展**：instance/health/锁仍是服务端基础 | S5 复用现有锁与 health，不推翻 | 原样保留；S5 增补并发冷启动、显式 stop、instance 不匹配 |

### S7 + S8 模型调用 trace 与读取（#333 / #334）

| 旧断言（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| `test_api.py::test_one_tool_round_emits_the_expected_event_sequence`、`test_s3_shared_execution.py::test_new_resumed_and_continued_rounds_emit_the_same_sequence` | 同一轮序新增轻量 `llm_call` 摘要事件（每次模型调用一次，位置在对应 `step_start` 之后、`final_answer` / `tool_call` 之前） | S7 明确要求 SSE 只发轻量摘要，事件序列是有意扩展 | 改写：原顺序保留，插入 `llm_call`；`check:sse` 与 §3.4 表同步 |
| `test_no_proxy_env.py::test_openai_client_constructs_after_normalization`（旧版先断言 `InvalidURL` 再手动清洗） | 适配器在构造 httpx 客户端前自行清洗；直接构造不再因括号 IPv6 失败 | S7 让 OpenAI 适配器自建 hook 客户端，必须像后端 runtime 一样自愈 | 改写为「构造前已清洗、环境已改写」，不再把可预见的启动失败当基线 |
| `test_trace.py` 的默认 `/trace` 读取/导出断言 | **保留**：默认不带参数时行为不变；新增 `type` / `call_id` / `offset` / `limit` | 读取合同是扩展，不是重写 | 原样保留；S7 已增补新参数 200/422，S8 继续扩展 200/404/413 与过滤分页断言 |
| `test_repl.py::test_rendered_event_types_are_the_small_face` | 终端小事件面新增 `llm_call` 一行摘要 + `/trace` 指针；不复制完整 Web 调试界面 | S8 要求终端保留调用概要及查看位置 | 改写；其余渲染 / 不打印 reasoning / 不泄露 secret 断言原样保留 |
| `test_reasoning.py::test_final_answer_event_carries_optional_redacted_reasoning` 的 `quiet_finals[-1] == {"answer": "ok"}` | `final_answer` 保留「无 reasoning 时省略可选字段」，但事件统一带 `round` / `step` / `plan_version` | S8 的修订元数据是显式扩展；继续断言整份 payload 相等会把新增字段当回归 | 改写：断言 `answer` 与 `reasoning` 缺省，不再锁死整份字典 |
| `test_sse_publish_ast.py` + `npm run check:sse` 的四处对齐 | **扩展**：新增 `llm_call` 必须同时进发布点、§3.4 表、`useSSE.ts`、`types/index.ts`、`eventLabels.ts` | 事件词汇表四处对齐的机械守卫不变 | 扩展，不替换 |
| `test_models.py::test_provider_error_key_never_reaches_task_trace_logs_or_sse` | **保留**：旧 `_LeakyClient` 直接重写 `complete()`，不进入 S7 记录包装器，因此该用例继续守任务错误/日志/SSE 面 | 保留原覆盖，不把它当成 `llm_call` 记录面的证明 | 原样保留；新增 `test_the_recorded_provider_error_is_redacted_by_the_manager`，让 provider 错误走真实 `LLMClient.complete` → `TraceRecorder` 路径，断言 trace 的 `llm_call` 记录带 `[redacted]` |
| trace 生命周期/删除断言（`test_qa_trace.py` + `test_llm_trace.py::test_trace_recorder_log_event_follows_the_existing_lifecycle`） | **保留**：`llm_call` 与 step/tool 共用同一生命周期与删除清理 | 不新建平行存储的前提 | 原样保留；S7 的删除与晚到写入竞态覆盖落在 `test_llm_trace.py`，不是 `test_qa_trace.py` |

### S12 文档收口

| 旧表述（位置） | 新合同 | 原因 | 迁移 / 退役去向 |
|---|---|---|---|
| README / architecture / GLOSSARY / 设置说明里的 `bash` 工具名、前台启动、旧模型 CRUD 描述 | 按届时实跑更新为 `shell`、`qianfan status` / `stop` / `serve`、当前模型面与 `llm_call` 读取 | 现态文档随代码，S0 不提前改成未来态 | S12 统一更新；S0 只落 ADR 0013 与修订指针 |

## 3. 使用方式

- 每片开工前先复跑第 1 节基线；红了先区分环境假红与实现变化，再动代码。
- 退役/改写测试时，按上表把**真实功能覆盖**迁到新接口；PR/提交说明写「退役哪条旧规格、
  新合同是什么、覆盖迁到哪」，不允许只写「测试更新」。
- 上表未列出的测试默认保留；若某片发现新的旧断言，补进本表并在该片 PR 中说明，不静默删除。
