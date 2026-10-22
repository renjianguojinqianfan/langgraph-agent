# 增量 PRD（P0 四件套）— 基于 LangGraph 的自主任务 Agent

> 文档性质：**增量 PRD（仅描述变更部分，不重写整份 PRD）**
> 产品经理：许清楚（Xu）　|　版本：v0.1（P0 增量草案）　|　日期：2025-08-24
> 关联文档：现有 PRD `docs/prd.md`（P0/P1/P2 分级）、架构 `docs/architecture.md`、本增量目标文件 `docs/incremental-prd-p0.md`
> 项目根目录：`E:\code\demo\langgraph-agent\`

---

## 0. 背景与对标依据

本次为**增量对齐**：把现有工程对标市面主流基础 Agent，补齐差距。**仅做 P0 四件套**，走轻量 SOP。

| 市面能力 | 本次对标项 | 说明 |
|----------|-----------|------|
| HelloAgents GSSC（长上下文压缩） | 项1 上下文压缩/管理 | 多步历史变长后截断或摘要早期消息，控制 Token 上限 |
| HelloAgents CircuitBreaker | 项2 工具熔断+重试退避 | 连续失败临时熔断 + 指数退避重试 |
| HelloAgents 插件式/热插拔工具注册 | 项3 插件式工具注册 | 从内置 4 类升级为可动态注册/加载外部工具 |
| HelloAgents TraceLogger 全链路日志 | 项4 持久化可观测 trace 落盘 | SSE 事件流同时落盘成可回放运行日志 |

> 注：现有 PRD 的 P1-3（持久化）、P1-4（重试与退避）**原已规划但代码未实现**（架构 §5.5 仅约定默认值、实际 `code_exec.py`/`http_api.py` 无重试逻辑）。本次增量将这两块的"落盘"与"熔断+重试"**提升为 P0 范围并补齐实现**；人工确认（P1-2）、鉴权（P1-6）等保持现状，不在本次增量范围。

---

## 1. 增量目标

### 一句话定位
在**不破坏现有编排内核与 SSE 推送**的前提下，补齐"上下文压缩、工具熔断+重试、插件式工具注册、可观测 trace 落盘"四项工程化能力，使 Agent 在长任务、弱网络、工具需扩展、事后审计四类场景下达到主流基础 Agent 水准。

### 四项各自目标与成功标准

| 项 | 目标 | 成功标准（可量化） |
|----|------|--------------------|
| **1 上下文压缩/管理** | 多步任务历史超阈值后，自动截断或 LLM 摘要早期消息，控制 Token 上限，避免上下文爆炸 | 消息估算 token 数超预算时压缩至 ≤ 预算；最近 `keep_recent` 条消息完整保留；任务成功率（SC1 70%）不显著下降（允许 ±10pp） |
| **2 工具熔断+重试退避** | 工具连续失败达阈值临时熔断（禁用一段时间）；调用失败按指数退避重试 | 连续失败达 `failure_threshold` 次后短路返回、不真正执行；冷却后 half-open 试探；瞬时失败在 `backoff_base*factor^n` 退避后重试成功 |
| **3 插件式工具注册** | 从"内置 4 类"升级为可动态注册/加载外部工具（Python 模块/目录自动发现），工具集可生长 | 合规 `BaseTool` 子类放入插件目录并重启即被自动发现、可被 LLM 调用；`BaseTool` 接口与内置工具零改动 |
| **4 持久化可观测 trace 落盘** | SSE 事件流同时落盘为可回放结构化日志（JSONL），供事后审计/调试 | 每帧 SSE 事件均落入 `<trace_dir>/<task_id>.jsonl` 且顺序一致；现有 SSE 推送零回归；新增接口可拉取回放 |

---

## 2. 用户故事（针对四项）

| # | 角色 | 用户故事 | 价值 |
|---|------|----------|------|
| US-P0-1 | 开发者 | 作为开发者，我希望长任务（如"调研 20 个竞品并出报告"）跑很久也不因上下文溢出而变傻/报错，从而稳定拿到结果。 | 长任务稳定 |
| US-P0-2 | 开发者 | 作为开发者，我希望某工具（搜索/API）临时抽风时 Agent 自动重试、连续坏掉时直接熔断跳过，而不是卡死或浪费大量 token。 | 韧性、省成本 |
| US-P0-3 | 开发者 | 作为开发者，我希望把一个新能力（如内部 API 封装）写成一个 `BaseTool` 丢进插件目录就能用，而不用改核心代码。 | 可扩展（呼应原 SC4） |
| US-P0-4 | 运维/调试者 | 作为运维/调试者，我希望任务跑完/跑挂后，能拉到一份完整、可回放的运行日志（每一步事件），用于审计与排查。 | 可观测、可审计 |
| US-P0-5 | 前端用户 | 作为前端用户，我希望在界面上看到工具熔断/重试状态与运行日志回放，而不是只看到最终成功/失败。 | 透明、可调试 |

---

## 3. 需求点（每项拆 P0 内子点 + 验收标准 + 改动点）

### 项1：上下文压缩/管理

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 1.1 | 触发阈值 | 以"估算 token 数"为主触发（`estimated_tokens ≈ chars/4`，中文保守计）；辅以消息条数兜底 `context_max_messages` | `estimated_tokens > context_token_budget` 或 `len(messages) > context_max_messages` 时触发压缩 | 新增 `backend/core/agent/context.py`：`estimate_tokens(messages)` |
| 1.2 | 压缩策略：截断 | 保留 system + 最近 `keep_recent` 条消息，丢弃早期；丢弃处插入 `{"role":"system","content":"[上下文已截断：前 N 步历史已省略]"}` 占位（**零额外 LLM 调用、确定性强**） | 压缩后 `estimated_tokens ≤ budget` 且最近 `keep_recent` 条原文完整；至少保留 system + 最近 1 个 assistant+tool 轮（**绝不清空全部**） | `context.py`：`compress_messages(messages, strategy, ...)` |
| 1.3 | 压缩策略：LLM 摘要（可选升级） | 把将被丢弃的早期消息交给 LLM 生成 ≤ `summary_max_tokens` 摘要块，替换原消息为单个 system 摘要；保留最近 `keep_recent` 条原文 | 摘要块 token 数 ≤ `summary_max_tokens`；任务成功率不显著下降（±10pp 内） | `context.py`：`summarize_messages(llm, messages)` |
| 1.4 | 压缩后结构 | messages 结构：`[system(原), summary/截断占位, *recent_messages]`；OpenAI 格式（role/content/tool_calls/tool_call_id）全部保留，不破坏 LangGraph 透传 | 压缩后消息能被 LLM 正常消费、正常产出 final_answer（冒烟验证） | `state.py`：`AgentState` 新增 `compressed: bool`、`context_tokens: int`（total=False，向后兼容） |
| 1.5 | 触发位置 | 在 `planner`/`executor` 进入 LLM 前统一调用压缩（避免每节点重复） | 每次 LLM 调用前 messages 已是最新压缩态 | `nodes.py`：`_build_messages` 前插入压缩；`config.py` 新增 `context_*` 配置 |

**配置项（config.py / .env.example 新增）**：`context_token_budget`、`context_max_messages`、`context_keep_recent`、`context_compress_strategy`(truncate|summarize)、`context_summary_max_tokens`。

---

### 项2：工具熔断 + 重试退避

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 2.1 | 失败计数与熔断 | 每工具一个 `CircuitBreaker`：`state∈{closed,open,half_open}`、`failure_count`、`opened_at`、`cooldown`；连续失败达 `failure_threshold` 进入 `open`，冷却内调用**短路**返回 `ToolResult(success=False, error="circuit open: <tool>")`，不真正执行 | 连续失败 `failure_threshold` 次后，第 (threshold+1) 次调用不执行且 `tool_result` 标记 `circuit_open=true`，事件流出现 `tool_circuit_open` | 新增 `backend/core/tools/resilience.py`：`CircuitBreaker`；`nodes.py`：`tool_node` 调用前查 breaker |
| 2.2 | 半开恢复 | 冷却结束进入 `half_open`，放行 1 次试探；成功则复位 `closed`/清零，失败则重新 `open` | 冷却 `cooldown` 秒后下一次试探调用被执行（half_open 路径可达） | `resilience.py`：`CircuitBreaker.allow()` |
| 2.3 | 指数退避重试 | 失败（success=False 或抛异常）重试至多 `max_retries` 次；`delay = backoff_base * (backoff_factor**attempt)`（默认 1s,2s…） | 单次瞬时失败且 retryable → 在退避后重试成功，`status=success`，重试次数 ≤ `max_retries` | `resilience.py`：`with_retry(...)`、`ToolExecutor.dispatch(tool, **kwargs)`（整合熔断+重试+事件发布） |
| 2.4 | 可重试语义 | 仅对"瞬时/幂等"失败重试（网络超时、5xx、429）；对明确 client error（参数错）不重试（由工具 `retryable` 标记决定） | `retryable=False` 的工具失败时**不重试**、直接返回 | `base.py`：`BaseTool` 新增 `max_retries`、`circuit_breaker`、`retryable` 类属性 |
| 2.5 | 适用工具范围 | 默认启用熔断+重试：`web_search`、`http_request`（网络类）；`code_exec` 默认 `max_retries=0`（或不重试非幂等）、`file_io` 默认 `max_retries=0` 无熔断 | 各自按配置生效；`code_exec`/`file_io` 不引入额外延迟 | `base.py` 各工具 override；`config.py` 全局默认 `tool_*` |

**新增 SSE 事件（不破坏现有协议，追加类型）**：`tool_circuit_open` → `{tool_name, cooldown_sec}`，供前端展示熔断状态。

**配置项（config.py / .env.example 新增）**：`tool_failure_threshold`、`tool_cooldown_sec`、`tool_backoff_base`、`tool_backoff_factor`、`tool_max_retries`（全局默认，工具可 override）。

---

### 项3：插件式工具注册（可扩展工具集）

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 3.1 | 自动发现机制 | 新增 `registry.discover_plugins(plugins_dir)`：用 `importlib` 扫描目录下所有 `*.py`（及子包 `__init__.py`）动态 import，触发其中 `@register`；`_REGISTRY` 行为不变 | 插件目录下放合规工具并重启 → 出现在 `build_tools()` 列表 | `registry.py`：新增 `discover_plugins`、`_import_module_from_path` |
| 3.2 | 接口零改动 | `@register` 装饰器与 `BaseTool` 契约完全不变；内置 4 类无需任何修改 | 不修改 `base.py`/内置工具即可接入插件 | `base.py`/`registry.py` 核心契约不动 |
| 3.3 | 启动接入 | `TaskManager.__init__` 在 `build_tools()` 之前调用 `discover_plugins(settings.plugins_dir)`；开关 `plugins_autoload`（默认 true），目录不存在则跳过、不报错 | `plugins_autoload=false` 或目录缺失 → 行为等同现状（零回归） | `task_manager.py`：`__init__` 增加发现调用；`config.py` 新增 `plugins_dir`、`plugins_autoload` |
| 3.4 | 示例插件 | 提供 `backend/plugins/example_tool.py`（最小 `BaseTool`+`@register`）作为模板 + 冒烟验证 | 端到端：示例插件被 LLM 成功调用一次 | 新增 `backend/plugins/__init__.py`、`example_tool.py` |
| 3.5 | 同名冲突 | 同名工具冲突时保留先注册者并写 warning 日志（避免静默覆盖） | 冲突场景下 `list_tools()` 含预期工具、有 warning | `registry.py`：`register`/`discover_plugins` 冲突处理 |
| 3.6 | 预留 OpenAPI 方向 | 新增 `registry.make_openapi_tool(spec)` **骨架/占位**（输入 OpenAPI spec → 返回 `BaseTool` 子类），**P0 不实现完整封装** | 接口签名存在、文档注明后续接入方式 | `registry.py`：占位函数 + 注释 |

**配置项（config.py / .env.example 新增）**：`plugins_dir`（默认 `backend/plugins/`）、`plugins_autoload`（默认 true）。

---

### 项4：持久化可观测 trace 落盘

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 4.1 | Trace 订阅落盘 | 新增 `TraceRecorder` 作为 EventBus **常驻订阅者**：任务创建时 `subscribe(task_id)`，每次 `publish` 追加写入 `<trace_dir>/<task_id>.jsonl`（每行 `{"type","data","ts"}`，与 EventBus 事件结构一致） | 每帧 SSE 事件均落盘、顺序与 SSE 一致 | 新增 `backend/services/trace.py`：`TraceRecorder`（或扩展 `persistence.py`） |
| 4.2 | 不破坏 SSE | trace 仅作为 EventBus 的另一个 subscriber，复用现有 fan-out；**不改动** `event_bus.py` 推送逻辑 | 现有 SSE 推送零回归（前端仍实时收到） | `event_bus.py` 不变；`task_manager.py`：create_task 内 `trace.attach(event_bus, task_id)` |
| 4.3 | 生命周期 | 任务结束（`finish`/`stop`）追加 `trace_end` 事件并 `close()`；文件按 `data_dir` 约束，目录不存在自动创建 | 任务结束后 `.jsonl` 完整、可解析 | `task_manager.py`：`run()` 结束 / `stop()` 调 `trace.close()` |
| 4.4 | 回放接口 | 新增 `GET /api/tasks/{id}/trace` → 返回 JSONL（`application/x-ndjson`）；可选 `?format=json` 返回解析后事件数组 | 接口返回完整事件流，前端可逐行回放 | `routes.py`：新增 trace 路由；`config.py` 新增 `trace_enabled`、`trace_dir` |

**配置项（config.py / .env.example 新增）**：`trace_enabled`（默认 true）、`trace_dir`（默认 `data/traces`）。

---

## 4. UI / 可视化影响（哪些项需要前端配合）

| 项 | 是否需要前端 | 草图要点 |
|----|--------------|----------|
| **项4 trace 回放** | ✅ 需要 | 任务详情页新增 **"Trace / 运行日志" Tab**（或右侧面板）：拉取 `GET /trace`，以时间线 + 可折叠 JSON 展示每个事件（type/ts/data）；顶部按 `type` 筛选；每行展开看 `data`；按钮"导出 .jsonl"。可复用 `StepTimeline` 样式，但展示更原始的事件流。 |
| **项2 熔断/重试状态** | ✅ 需要 | `StepTimeline` / `StepDetail` 中：当 `tool_result` 带 `circuit_open=true` 或 `retried>0` 时显示醒目徽章（红色"⚡熔断"、黄色"↻重试×2"）；收到 `tool_circuit_open` 事件时，对应 step 显示"工具 X 已熔断（冷却 Ns）"。`StepDetail` 增加"熔断/重试"状态行；时间线节点态变色。 |
| **项1 压缩指示** | ⚠️ 可选 | 在状态压缩时发轻量事件 `context_compressed`（或 `step_start` 携带标记），`StepTimeline` 显示"🗜 上下文已于 Step N 压缩"小标记。非强需求，可后置。 |
| **项3 插件清单** | ⚠️ 可选 | 设置/诊断面板展示当前已加载工具列表（含"内置/插件"来源），便于验证插件生效。非强需求。 |

### 前端草图（trace 回放 + 熔断状态，ASCII 要点）

```
┌───────────────┬──────────────────────────────────────┬──────────────┐
│ 历史任务       │  任务：调研 20 个竞品并出报告          │ Step 详情     │
│ • 任务A   ✓    │  [消息流] [步骤] [📜 Trace]            │              │
│ • 任务B   ⟳    │  ── Trace 视图 ──                     │ 工具: search │
│               │  ▸ step_start  12:01:03                │ 状态: ⚡熔断  │
│               │  ▸ tool_call  web_search               │ 冷却: 28s    │
│               │  ▸ tool_circuit_open web_search ⚡     │ ↻ 重试×2 ✓  │
│               │  ▸ tool_result (circuit_open=true)     │              │
│               │  ▸ 🗜 上下文已于 Step 5 压缩            │ [导出.jsonl] │
│               │  [按类型筛选 ▾]  [展开 data ▸]          │              │
└───────────────┴──────────────────────────────────────┴──────────────┘
```

---

## 5. 待确认问题（极少量）

| # | 问题 | 建议默认值 | 影响 |
|---|------|-----------|------|
| Q1 | 压缩阈值默认值：token 预算（如 `context_token_budget=8000`）还是字符/消息条数？中文场景估算口径（`chars/4` 是否够保守）？ | `context_token_budget=8000`，`context_keep_recent=10`，策略默认 `truncate` | 影响项1 行为 |
| Q2 | 熔断阈值默认值：连续失败次数 / 冷却时长 / 退避系数？ | `failure_threshold=3`、`cooldown_sec=30`、`backoff_base=1`、`backoff_factor=2`、`max_retries=2` | 影响项2 行为 |
| Q3 | 插件目录位置与要求：目录名 `backend/plugins/`？是否要求 `__init__.py`？子包是否递归发现？ | `plugins_dir=backend/plugins/`，递归发现，允许无 `__init__.py` 的单文件模块 | 影响项3 落点 |

> 以上 3 项均不阻塞开发，文档已给默认值；确认后写入 `.env.example` 即可。

---

## 6. 范围边界（In / Out）

### 6.1 范围内（In Scope，本次 P0 增量）
- **项1** 上下文压缩/管理（截断 + 可选 LLM 摘要）。
- **项2** 工具熔断 + 指数退避重试（复用/补齐原 P1-4）。
- **项3** 插件式工具注册（目录自动发现 + OpenAPI 方向占位）。
- **项4** 持久化可观测 trace 落盘（复用 EventBus 订阅，补齐原 P1-3 落盘）。

### 6.2 范围外（Out of Scope，本次不做）
- **P1 辅助模型 / 主辅模型分工**（DeepAgent 的 sub-model）—— 不做。
- **RAG / 自建知识库** —— 不做（除非作为外部工具调用）。
- **子 Agent / 多 Agent 协作**（原 P2-1）—— 不做。
- **规划期风险扫描**（planning-phase risk scan）—— 不做。
- **OpenAPI 工具封装的完整实现** —— 仅预留 `make_openapi_tool` 骨架，不实现封装。
- **成本/用量统计面板**（原 P2-3）—— 不做。
- **人工确认（原 P1-2）、鉴权（原 P1-6）** —— 已有，保持现状，不在本增量范围。

---

## 附：增量对现有模块的改动汇总

| 现有模块 | 本增量改动 |
|----------|-----------|
| `backend/core/agent/state.py` | 新增 `compressed`、`context_tokens` 字段 |
| `backend/core/agent/nodes.py` | 压缩接入（`_build_messages` 前）；`tool_node` 改用 `ToolExecutor.dispatch`（熔断+重试）；发 `tool_circuit_open` 事件 |
| `backend/core/tools/base.py` | `BaseTool` 新增 `max_retries`/`circuit_breaker`/`retryable` 类属性（接口契约不变） |
| `backend/core/tools/registry.py` | 新增 `discover_plugins`、`make_openapi_tool`（占位）；冲突处理 |
| `backend/core/tools/resilience.py` | **新增**：`CircuitBreaker`、`with_retry`、`ToolExecutor` |
| `backend/core/agent/context.py` | **新增**：`estimate_tokens`、`compress_messages`、`summarize_messages` |
| `backend/services/event_bus.py` | **不改动**（仅作为 subscriber 接入） |
| `backend/services/persistence.py` 或 `backend/services/trace.py` | **新增** `TraceRecorder` |
| `backend/services/task_manager.py` | 启动时 `discover_plugins`；`create_task` 挂 trace；结束 `trace.close()` |
| `backend/api/routes.py` | 新增 `GET /api/tasks/{id}/trace` |
| `backend/api/sse.py` 协议 | 追加事件类型 `tool_circuit_open`（可选 `context_compressed`） |
| `backend/config.py` + `.env.example` | 新增 `context_*` / `tool_*` / `plugins_*` / `trace_*` 配置 |
| `backend/plugins/__init__.py` + `example_tool.py` | **新增**：示例插件 |
