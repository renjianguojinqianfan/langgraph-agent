# AGENTS.md - langgraph-agent

> 本文件是 `E:\code\demo\langgraph-agent` 的 Agent 操作手册 / 核心指令集。不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律，只写本目录特有的东西。

## 1. 快照
基于 **LangGraph（StateGraph）** 的自主任务 Agent 平台，前后端一体：自然语言下发任务 → Agent 自主规划（planner→executor→tool→reflect 循环）→ 调用工具完成多步任务 → SSE 实时可视化。经 v1 + P0（对齐 HelloAgents/DeepAgent）+ P1（六项能力）+ P2（MCP/Git）+ P3（断点续跑，Issue #4）多轮迭代，**351 个离线测试全绿** + 真实 LLM（qwen3.6-plus）端到端与断点续跑双场景验证通过。

## 2. 硬规则（改前必读）
- **测试环境隔离**：离线测试必须不受本地 `.env` 影响——`backend/tests/conftest.py` 顶部用环境变量覆盖（`LLM_BASE_URL=""` / `USE_MOCK_LLM=true` / `AUX_LLM_ENABLED=false` / `AUTH_ENABLED=false` / `OPENAPI_ENABLED=false` / `CHECKPOINT_ENABLED=false`）。**改 conftest 时勿破坏这段隔离**，否则本地 live 配置会污染全部离线用例。
- **P0 死循环修复勿动**：`backend/core/agent/nodes.py` 的 `human_confirm_node` 中 `_needs_confirm` 重算逻辑（确认/拒绝后重算是否仍有待确认项）是 P0 修复的死循环根因，**一字不动**；风险确认复用该机制，新增确认逻辑必须走 `_confirmed_ids/_rejected_ids` 流程。（P3 在该节点 else 分支追加了 stop-forced 的 `pending_confirm` 标记，位于重算块之前，属于 Issue #4 行为，保留。）
- **熔断层零改动**：`backend/core/tools/resilience.py`（CircuitBreaker/with_retry/ToolExecutor）与 `backend/core/tools/registry.py` 是 P0/P2 的多轮约束对象，改动需极谨慎——MCP/Git/OpenAPI 工具一律由 `task_manager.py` 显式追加（`_load_mcp_tools`/`_load_git_tools`），**不经 @register**。（CI 已由 `guard-protected-files` job 机械化拦截这两个文件的改动，逃生舱：PR/commit 标题含 `[OVERRIDE]`。）
- **真实 LLM 验证**：离线测试只证明确定性；发布前/换供应商用 `scripts/live_e2e.py` 跑真实模型（`LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_e2e.py`，含场景 1 冒烟 + 场景 2 断点续跑）。Key 只走环境变量，**绝不硬编码、绝不打印明文**；`.env` 的 `llm_api_key` 留空靠环境变量注入。
- **解释器**：pytest 一律用 `.venv311/Scripts/python.exe`（Python 3.11.15，已装依赖；根目录 `.venv` 是 3.13 无依赖，不要用）。
- **端口**：后端 8000、前端 dev 5173。勿与 `trae` 项目同时跑（同端口冲突）。

### P3 断点续跑专项约束（Issue #4）
- **checkpoint 副本语义**：挂载 checkpointer 后 langgraph 每 superstep 向节点传 state **副本**——任何跨节点通信不得再依赖共享字典引用，必须走 TaskManager 权威信号（`_stop_flags` / `is_stop_flagged()`），nodes 侧统一经 `_stopped(state)` helper 轮询并写回副本。
- **resume 拒绝语义不可放松**：非 INTERRUPTED、无 checkpoint、停在确认闸口（pending_confirm 标记）三类一律 409 同步拒绝——闸门永不被静默绕过。
- **依赖矩阵**：`langgraph-checkpoint-sqlite==2.0.11` 钉死配套 langgraph-checkpoint 2.1.2；Dependabot 的 3.x 升级 PR 已知不兼容，勿合并。

## 3. 目录结构
| 路径 | 内容 |
|---|---|
| `backend/config.py` | Settings（15 组配置前缀：llm/context/tool/plugins/trace/risk/subagent/kb/aux_llm/auth/openapi/mcp/git/checkpoint/sandbox）|
| `backend/core/agent/` | 编排：state / nodes（planner/executor/tool/reflect/risk_scan/subagent_split/human_confirm）/ graph（mode=main\|subtask）/ context（压缩）/ risk（EHRB）/ subagent |
| `backend/core/tools/` | BaseTool 规范 + 14+ 工具：web_search/file_io/code_exec/http_request/memory_search/kb_query/spawn_subagent/git_*(7)/McpTool/OpenAPITool + resilience(熔断) + registry(插件发现) |
| `backend/core/llm/` | LLMClient 抽象 + OpenAI 兼容工厂 + Mock/Aux |
| `backend/core/kb/` | KnowledgeBase（标准库关键词索引，离线可用）|
| `backend/core/mcp/` | McpClientManager（stdio 传输，每 server 一线程+事件循环）|
| `backend/services/` | event_bus / trace(JSONL) / persistence / task_manager / auth(hmac) |
| `backend/api/` | routes（14 REST）/ sse / schemas |
| `backend/plugins/` | 插件目录（自动发现 BaseTool，example_tool.py）|
| `backend/tests/` | **40 文件 / 351 用例**（含 test_qa_* 独立补充；test_checkpointer/test_resume/test_orphan_reconcile 为 P3）|
| `frontend/` | React 三栏 UI：TaskPanel/TraceTab/RiskBanner/SubtaskList/KbPanel/LoginPage 等 14 组件 |
| `docs/` | prd / architecture / 增量 PRD+架构（p0/p1/p2/p3-resume）|
| `.agents/skills/` | 千问官方 skills（model-selector/ops-auth/usage）。集成路径：references 由 `scripts/live_skill_test.py` 复制到 `data/kb/qianwen-skills/` 并重建索引 → 真实模型任务中经 `kb_query`/`memory_search` 工具检索；该脚本同时验证"KB 命中 + 答案给出具体模型"全链路 |
| `scripts/` | live_e2e.py（真实 LLM 验证，`--check` 为无 Key 离线冒烟）/ live_skill_test.py（skills→KB→真实模型）|
| `data/` | 运行时生成（tasks.json/traces/kb/artifacts），不入库 |

## 4. 常用命令
```bash
# 后端（要求已建 .venv311 并装依赖）
.\.venv311\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000

# 全部离线测试（唯一权威回归）
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q

# 真实 LLM 端到端（发布前/换供应商）
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_e2e.py

# 离线冒烟（无 Key/无网络，验证脚本依赖链可装配；已接入 CI）
.\.venv311\Scripts\python.exe scripts/live_e2e.py --check

# skills 知识库联测（skills references → KB → 真实模型）
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_skill_test.py

# 前端（先 cd frontend，Node 22）
npm run dev          # 5173，proxy /api 到 8000
npx tsc --noEmit     # 类型检查 0 错误
```

## 5. 完成定义
- 后端 `.venv311 python -m pytest backend/tests/ -q` 全绿（351）；改前端时 `npx tsc --noEmit` 0 错误。
- 改动跑通真实模型冒烟（有 Key 时）：`scripts/live_e2e.py` PASS。
- 改接口/配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。
- 新依赖需说明理由；**避免升级 uvicorn/starlette**（mcp 依赖冲突教训：用 `--no-deps` 装 mcp）。
- 新副作用（写库/建文件/起服务）可逆或已口述说明。

## 6. 行为边界
- ✅ 允：改 `backend/`、`frontend/`、`tests/`、`docs/`、`scripts/`；修 bug 不加多余特性。
- ⚠️ 需确认：改 `_needs_confirm` 重算、改 `resilience.py`/`registry.py`、改 conftest 隔离、加第三方依赖、动端口。
- ⛔ 禁止：提交 `.env`/`data/`/`frontend/dist` 里的密钥或生成物；绕过确认直接执行危险工具（code_exec/git_commit/http 写类/MCP 写类）；硬编码密钥；`git push --force` 类危险命令（黑名单默认拒绝）。
