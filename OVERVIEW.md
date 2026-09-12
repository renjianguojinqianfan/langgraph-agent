# LangGraph 自主任务 Agent — 交付概览

## TL;DR
已交付一个基于 **LangGraph（Python）** 的「自主任务 Agent」完整工程产品（前后端全覆盖）。经 QA 独立离线测试 **62/62 全绿（100%）**，验收通过。

## 交付状态
- PRD → 架构设计 → 代码实现 → QA 验收 四阶段全部完成
- 测试通过率：**62 passed / 0 failed（100%）**
- 修复记录：Round 1 发现 2 个源码根因（human_confirm 死循环、llm_base_url 默认值过强），工程师修复、Round 2 回归确认全绿
- 已知问题：无功能性缺陷；仅 Windows 沙箱下 pytest 临时目录清理的无害告警

## 架构要点
- **编排内核**：LangGraph `StateGraph` 的 Planner→Executor→Tool→Reflect 循环（条件边），含 `human_confirm` 中断节点与 ≤2s 停止检测，`max_steps` 默认 15
- **后端**：Python + LangGraph + FastAPI + uvicorn，进程内 `EventBus` 经 SSE 实时推送，内存 + JSON 持久化
- **前端**：React 18 + Vite + TypeScript + Tailwind + Zustand，三栏布局（历史任务 / 任务流 / Step 详情），SSE 驱动实时渲染，含停止按钮与人工确认弹窗
- **LLM 抽象层**：OpenAI 兼容 `LLMClient`，支持 OpenAI / DeepSeek / Ollama + 可注入 Mock（便于离线测试）
- **工具层（BaseTool 规范）**：web_search（DuckDuckGo 可插拔）、file_io（沙箱白名单）、code_exec（受限 subprocess 沙箱）、http_api（写方法需确认）

## 文件清单（工程根 `E:\code\demo\langgraph-agent\`）
- `backend/`（~31 文件）：`config.py`、`core/llm/`、`core/tools/`、`core/agent/`、`services/`、`api/`、`main.py`
- `frontend/`：React 三栏可视化 UI（`src/components`、`src/store`、`src/api`、`src/hooks`）
- `docs/`：`prd.md`、`architecture.md`、`class-diagram.mermaid`、`sequence-diagram.mermaid`
- `backend/tests/`：62 个离线测试用例
- 启动/配置：`README.md`、`requirements.txt`、`package.json`、`.env.example`、`start.py`、`docker-compose.yml`

## 启动方式
```bash
# 后端（Python 3.10+，本地验证用 3.11.15）
cd langgraph-agent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 填 llm_api_key，或设 use_mock_llm=true 离线跑
uvicorn backend.main:app --reload --port 8000

# 前端
cd frontend && npm install && npm run dev          # http://localhost:5173

# 一键 / 离线验证
python start.py --mock        # 一键启动（mock 模式）
python backend/tests/test_smoke.py   # 离线 smoke
```

## 下一步建议
1. 在 `.env` 填入 LLM Key（或设 `use_mock_llm=true` 先离线体验端到端流程）
2. 前后端都起来后，提交一个自然语言任务（如「创建一个文本文件并写入内容」）观察多步编排与可视化
3. 4 项默认配置可随时调整（无需改核心代码）：LLM 供应商/Key、`max_steps`、检索源（DuckDuckGo↔SerpAPI）、鉴权范围（v1 仅本地 demo 无鉴权）
4. 可选增强：装 `pytest-cov` 量化覆盖率；生产化加登录鉴权、Docker 代码沙箱、SQLite 持久化

## 团队分工（SoftwareCompany SOP）
- 许清楚（产品经理）：PRD
- 高见远（架构师）：系统设计与任务拆解（T01–T09）
- 寇豆码（工程师）：全量编码实现（IS_PASS=YES）
- 严过关（QA）：独立测试验收（62/62 全绿）

---

# P0 增量（对齐市面主流基础 Agent）— 交付概览

## TL;DR
在既有自主任务 Agent 上补齐 P0 四件套（对标 HelloAgents / DeepAgent）：**上下文压缩、工具熔断+重试退避、插件式工具注册、持久化 trace 落盘**。QA 独立验收 **141/141 全绿（100%）**，零回归、零新增第三方依赖。

## 交付状态（2026-08-24）
- 增量 PRD → 增量架构 → 工程师改造 → QA 验收 轻量 SOP 全部完成
- 测试通过率：**141 passed / 0 failed（100%）**（62 原有 + 45 工程师新增 + 34 QA 独立补充）
- 路由判定：NoOne（无需返工，第 1 轮即全绿）
- 已知问题：无功能性缺陷；4 条非阻断观察项（half-open 并发语义、keep_recent 硬约束、既有 stop 竞态、插件全局注册）

## 增量能力说明
| 项 | 能力 | 落点 |
|----|------|------|
| 1 | **上下文压缩**：超阈值（默认 8000 tokens / keep_recent=10）自动截断早期消息，可选 LLM 摘要，控制 Token 上限 | `backend/core/agent/context.py` + `nodes.py._build_messages` + `state.py` |
| 2 | **工具熔断+重试退避**：连续失败达阈值（默认 3）短路，冷却 30s 后 half-open 试探；指数退避重试（base=1, factor=2, max=2）；发 `tool_circuit_open` 事件 | `backend/core/tools/resilience.py`（ToolExecutor 包装层）+ `nodes.py.tool_node` + 4 内置工具类属性 |
| 3 | **插件式工具注册**：`importlib` 递归扫描 `backend/plugins/` 自动发现合规 BaseTool，与内置工具共享 `_REGISTRY`，同名冲突保留先注册者 | `registry.py` + `backend/plugins/{__init__,example_tool}.py` + `task_manager.py` |
| 4 | **持久化 trace 落盘**：TraceRecorder 常驻订阅 EventBus 落 JSONL，顺序与 SSE 一致，`trace_end` 收尾；新增 `GET /api/tasks/{id}/trace`（NDJSON / ?format=json） | `backend/services/trace.py` + `task_manager.py` + `routes.py` |

## 关键约束验证
- `event_bus.py` / `graph.py` / `requirements.txt` **零改动**（TraceRecorder 仅作另一 subscriber，熔断不改 BaseTool.run 签名，压缩仅一处接入）
- 零新增第三方依赖（仅标准库 importlib/json/pathlib/threading/time）
- 前端仅类型小同步（`frontend/src/types/index.ts`：circuit_open/retries/tool_circuit_open/context_compressed），组件逻辑未动
- 新增 SSE 事件：`tool_circuit_open`（必做）、`context_compressed`（可选）；新增 REST：`GET /api/tasks/{id}/trace`
- 新配置项（均带默认）：`context_*` / `tool_*` / `plugins_*` / `trace_*`，见 `.env.example`

## 本轮文档
- `docs/incremental-prd-p0.md`（增量 PRD）
- `docs/incremental-arch-p0.md`（增量架构 + 任务拆解 T01–T05）
- `docs/incremental-class-diagram.mermaid` / `docs/incremental-sequence-diagram.mermaid`
- `backend/tests/`：原 62 + test_context(13) + test_resilience(16) + test_plugins(8) + test_trace(8) + test_qa_*(34)

## 待办/后续
- 前端 Trace 回放界面、熔断/压缩徽章展示（事件与字段已预留，未实现）
- P1 项：辅助模型分工、RAG/知识库、子 Agent、规划期风险扫描、OpenAPI 工具封装完整实现

---

# P1 完整增量（6 项能力）— 交付概览

## TL;DR
在 P0 + 前端补全基础上，落地 P1 完整六项能力：**规划期风险扫描（EHRB）、子 Agent 协作、RAG+跨会话记忆、辅助模型分工、基础鉴权、OpenAPI 工具封装**。QA 独立验收 **250/250 全绿（100%）**，路由判定 NoOne（无源码缺陷），仅 5 项非阻塞 P2 候选。

## 交付状态（2026-08-24）
- 轻量 SOP：增量 PRD → 增量架构 → 工程师实现 → QA 验收 全部完成
- 测试通过率：**250 passed / 0 failed（100%）**（141 原有 + 71 工程师新增 + 38 QA 独立补充）
- 工程师自验证：212 全绿 + 前端 `tsc --noEmit` 0 错误，IS_PASS=YES
- 新增依赖：仅 `pyyaml>=6.0,<7.0`（OpenAPI spec 解析）；其余全部标准库
- 关键约束：`human_confirm` 重算逻辑一字未动（风险确认复用 P0 机制）、`sse.py` 零改动、`AgentRuntime(task_manager=None)` 测试兼容

## 6 项能力落点
| 项 | 能力 | 落点 |
|----|------|------|
| 1 | **规划期风险扫描 EHRB**：planner→risk_scan 间插，五类危险词表（删除/破坏/财务/隐私/通信）+ 可选语义分析；高危操作复用 human_confirm 确认；事件 risk_report/risk_found | `core/agent/risk.py` + `nodes.py` + `graph.py` + `state.py` |
| 2 | **子 Agent 协作**：隔离子任务（独立 AgentState + 独立 EventBus 频道 + 线程池并行），主 messages 只留折叠摘要，防递归；事件 subtask_start/result/failed | `core/agent/subagent.py` + `core/tools/subagent_tool.py` + `graph.py(mode)` |
| 3 | **RAG+跨会话记忆**：标准库关键词索引（CJK 按字切分），.index.json 持久化，离线可用；memory_search/kb_query 工具 + 产物自动入库；REST GET/POST/DELETE /api/kb | `core/kb/knowledge_base.py` + `core/tools/kb_tools.py` + `routes.py` |
| 4 | **辅助模型分工**：aux_llm_* 配置（默认关闭），未配置零额外 LLM 调用；风险语义/摘要降级路径 | `core/llm/client.py(MockAuxLLMClient)` + `openai_compat.py` + `nodes.py(惰性 aux_llm)` |
| 5 | **基础鉴权**：TokenIssuer（hmac 签发/校验/过期），FastAPI Depends 依赖注入，默认关闭零回归；SSE ?token= | `services/auth.py` + `routes.py` + 前端 LoginPage/AuthGuard |
| 6 | **OpenAPI 工具封装**：YAML/JSON/URL 加载 spec，每 operation 生成 OpenAPITool（apiKey header/query、路径参数注入、4xx/5xx→false）；无效 spec 不中断启动 | `core/tools/openapi_tool.py` + `registry.py(make_openapi_tool 真实实现)` |

## 新增契约
- SSE 事件：`risk_report` / `risk_found` / `subtask_start` / `subtask_result` / `subtask_failed`
- REST：`POST /api/auth/token`、`GET /api/kb`、`POST /api/kb/rebuild`、`DELETE /api/kb/{doc_id}`；`/health` 返回 auth_enabled
- 配置前缀：`risk_*` / `subagent_*` / `kb_*` / `aux_llm_*` / `auth_token_ttl_sec` / `openapi_*`（默认值见 PRD Q1–Q6 / `.env.example`）
- 前端：登录页 + AuthGuard + 风险横幅（RiskBanner）+ 子任务列表（SubtaskList）+ 知识库面板（KbPanel）

## 已知问题（非阻塞，P2 候选）
1. `KnowledgeBase.add_document()` 不校验扩展名（二进制文件会索引乱码；入口已过滤文本类，实际安全）
2. 中文检索单字 token 化精度（共享单字也命中；架构定位为"最小可用"）
3. OpenAPI 缺 operationId 生成 `get__pets__petId` 双下划线风格（name 唯一可用）
4. 子任务 LLM 错误未映射 subtask_failed（已满足"失败不崩溃主任务"，语义待 P2）
5. 风险确认粒度为轮次级（架构 §9 声明，符合验收）

---

# 真实 LLM 接入（Live E2E）— 交付概览

## TL;DR
项目 250 个离线测试全部基于 MockLLM；新增**真实 OpenAI 兼容 LLM 端到端验证**能力（已验证 QianWen/DashScope `qwen3.6-plus`），用于发布前/换供应商时验证真实世界兼容性。含 `scripts/live_e2e.py` 验证脚本 + `scripts/LIVE_E2E.md` 接入指南。

## 已验证（2026-08-24）
- **真实模型端到端 PASS**：自然语言任务 → 真实模型规划 → 调用 file_io 写入 `agent_summary.txt` → final_answer → COMPLETED，9/9 检查通过
- 事件流含 `plan_update → tool_call → tool_result → artifact_created → final_answer → task_completed`（P1 risk_report 真实链路同样生效）
- 实测：qwen-turbo/qwen-plus 免费额度耗尽，**qwen3.6-plus 可用**；端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`

## 重要修复（整理中发现）
**离线测试与本地 `.env` 隔离**：为 live e2e 创建 `.env` 后，250 个离线测试被真实端点污染（Settings 从 `.env` 读到 DashScope base_url）。已在 `backend/tests/conftest.py` 顶部用环境变量覆盖（`LLM_BASE_URL=""` / `USE_MOCK_LLM=true` 等，环境变量优先级高于 `.env`），测试恢复确定性。这是"本地 live 配置不影响离线测试"的关键约定。

## 文件
- `scripts/live_e2e.py`：真实 LLM 端到端验证脚本（独立于 pytest）
- `scripts/LIVE_E2E.md`：接入指南（前置条件/额度探测/配置/运行/换供应商/安全规范）
- `backend/tests/conftest.py`：离线环境隔离（防 `.env` 污染）

## 运行
```bash
LLM_API_KEY="$DASHSCOPE_API_KEY" .venv311/Scripts/python.exe scripts/live_e2e.py
```

## 本轮文档
- `docs/incremental-prd-p1.md`、`docs/incremental-arch-p1.md`、`docs/incremental-class-diagram-p1.mermaid`、`docs/incremental-sequence-diagram-p1.mermaid`
- `backend/tests/`：141 原有 + test_risk/subagent/kb/aux/auth/openapi_tool/p1_integration（71）+ test_qa_p1_*（38）

---

# P1 增量（前端补全）— 交付概览

## TL;DR
把 P0 预留的 trace / 熔断 / 压缩能力在前端可视化：新增 **Trace 回放 Tab**（时间线 + 可折叠 JSON + 类型筛选 + 导出 .jsonl）、**熔断 ⚡ / 重试 ↻ 徽章**、**🗜 压缩标记**。QA 两轮验证通过（路由判定 NoOne），后端零改动。

## 交付状态（2026-08-24）
- 快速模式：工程师实现 → QA 独立验证 → 修复 2 项非阻塞建议 → 第 2 轮回归全过
- 编译/类型：`tsc --noEmit` 0 错误、`vite build` 通过（58 modules）
- 后端回归：`pytest backend/tests/ -q` **141 passed** 零回归（后端本轮零改动）

## 改动文件（全部在 frontend/，后端零改动）
- 新增：`components/TraceTab.tsx`
- 修改：`types/index.ts`（新增 ToolCircuitOpenData/ContextCompressedData/TraceMarker 等）、`api/client.ts`（getTaskTrace 默认 ndjson + raw 原样保留；导出 API_BASE）、`store/taskStore.ts`（per-task markers + tool_result 合并 circuit_open/retries）、`hooks/useSSE.ts`（补订阅 tool_circuit_open/context_compressed/trace_end；SSE 走 API_BASE）、`pages/TaskView.tsx`（tab 状态）、`components/TaskPanel.tsx`（Tab 栏）、`StepTimeline.tsx`/`StepDetail.tsx`（徽章）、`MessageStream.tsx`（熔断/压缩气泡）

## 关键点
- Trace Tab：先请求默认 ndjson，逐行解析 events（时间线/筛选），`raw` 原样保留供导出，导出即后端磁盘原始字节
- 徽章：`circuit_open=true` → ⚡熔断（红）、`retries>0` → ↻重试×N（黄）；`tool_circuit_open` 事件 → 任务流红色气泡
- 压缩标记：`context_compressed` 事件 → 紫色气泡「🗜 上下文已压缩：丢弃 N 条早期消息…」，markers 按 task_id 隔离、上限 200
- 关键修复（工程师）：P0 时代 useSSE 只声明未订阅这些事件类型，事件到不了前端，已补齐

## 已知说明项（非阻塞）
- 运行中任务打开 Trace 为静态快照（一次性 GET，无轮询）；heartbeat 不经 EventBus 不入 trace；跨任务 events 数组为既有行为

---

# P2 增量（MCP 客户端 + Git 工具）— 交付概览

## TL;DR
补齐对标 Claude Code/Codex 的两大核心差距：**MCP 客户端接入**（解锁外部工具生态，工具动态注册为 BaseTool）与 **Git 工具**（7 个 BaseTool 仓库内操作）。QA 独立验收 **331/331 全绿（100%）**，路由判定 NoOne。

## 交付状态（2026-08-24）
- 轻量 SOP：增量 PRD → 增量架构 → 工程师实现 → QA 验收 全部完成
- 测试通过率：**331 passed / 0 failed（100%）**（250 原有 + 39 工程师新增 + 42 QA 独立补充）
- 新增依赖：`mcp>=1.2,<2.0`（httpx 放宽 `>=0.27,<0.29`）；前端零新增依赖
- 关键约束：`resilience.py` / `registry.py` / `_needs_confirm` 重算逻辑 **零改动**（熔断复用、P0 修复不动）

## 两项能力落点
| 项 | 能力 | 落点 |
|----|------|------|
| 1 | **MCP 客户端接入**：stdio 传输连接外部 MCP server（每 server 一线程+独立事件循环），initialize+list_tools 动态注册工具（name=mcp__{server}__{tool}，args_schema 直取 inputSchema），call_tool 转发映射 ToolResult；失败隔离（连接失败仅 warning、杀进程后调用失败不崩溃）、复用 P0 熔断重试、写类工具 retryable=False 防重复副作用、per-call 确认启发式+force 覆盖、cleanup 幂等、GET /api/mcp/servers | `core/mcp/client.py` + `core/tools/mcp_tool.py` + `task_manager.py` + `routes.py` + `main.py(lifespan)` |
| 2 | **Git 工具**：7 个 BaseTool（status/diff/commit/log/branch/checkout/init），参数化 subprocess 无 shell（防命令注入）、白名单 verb + 黑名单（push/reset/clean/rebase/merge 等拒绝）+ 选项注入防护（--force/-f/--hard/-D 拒绝）、路径 is_relative_to 越界拒绝、非 git 目录 rev-parse 探测、改写类 requires_confirm | `core/tools/git_tools.py`（GitToolRunner）+ `task_manager.py(_load_git_tools)` |

## 新增契约
- Settings：`mcp_enabled/mcp_servers/mcp_timeout_sec/mcp_connect_timeout_sec/mcp_force_confirm`、`git_enabled/git_repo_dir/git_timeout_sec`（含派生属性）
- REST：`GET /api/mcp/servers` → {servers:[McpServerInfo{name,transport,status,tools_count,error}]}
- 不新增 SSE 事件（MCP 连接是平台级信息，用 REST 轮询）
- 前端：ConfirmDialog 确认标题分类（git_*→"Git 操作确认"、mcp__*→"MCP 工具确认"）、StepDetail 对 diff/log 用 `<pre>` 渲染

## 工程师额外修复（3 个遗留问题）
1. **mcp 依赖冲突**：pip 直装 mcp 会升级 uvicorn/starlette 破坏 fastapi——用 `--no-deps` 安装 mcp 1.29.0 + 客户端所需依赖，uvicorn 0.30.6/starlette 0.38.6 保持不动
2. **P1 循环导入**：`import backend.main` 必失败（graph→nodes→tools→subagent→graph 环），subagent.py 惰性导入修复
3. **smoke 竞态**：TaskManager 终态事件 publish 移到 save 之前（事件先可见，消除偶发失败）

## 已知问题（非阻塞）
- a) `_McpSession._proc` 从未赋值，cleanup 的 terminate 兜底为死代码（SDK stdio 自身清理有效，测试无泄漏）
- b) git_status/diff/log 的 path 参数实现为 `git -C <subdir>`（切换工作目录），父目录变更以 `../` 前缀出现，与"scope the status"语义有轻微偏差（不影响安全）

## 本轮文档
- `docs/incremental-prd-p2.md`、`docs/incremental-arch-p2.md`、`docs/incremental-class-diagram-p2.mermaid`、`docs/incremental-sequence-diagram-p2.mermaid`
- `backend/tests/`：原 289 + test_p2_mcp(15) + test_p2_git(24) + test_qa_p2_mcp(16) + test_qa_p2_git(18) + test_qa_p2_integration(8)

---

# P3 增量（断点续跑 / LangGraph Checkpointer）— 交付概览

## TL;DR
给任务内核装上 LangGraph `SqliteSaver` 检查点：停止的任务可 `POST /resume` 从断点复活，崩溃遗留孤儿任务启动自动对账。QA 基线 **351/351 全绿**（331 零回归 + 20 新增）+ 真模型双场景 PASS（基础冒烟 + stop→重建→resume→完成）。Spec 与验收全程见 [Issue #4](https://github.com/renjianguojinqianfan/langgraph-agent/issues/4)。

## 交付状态（2026-08-27）
- TDD 四轮红绿循环 + 双验证员 review ×2 轮；离线/在线证据链完整
- 新增依赖：`langgraph-checkpoint-sqlite==2.0.11`（钉版配套 langgraph-checkpoint 2.1.2）+ `aiosqlite`

## 能力落点
| 项 | 说明 | 落点 |
|----|------|------|
| 断点续跑 | `POST /api/tasks/{id}/resume`：join 收尾 worker → checkpoint 双探测 → 锁内 CAS claim → 恢复快照仅重置控制位 → 同 thread_id 重 invoke | `task_manager.py`(resume/_resume_run/_thread_config) |
| 孤儿对账 | 启动时 RUNNING/PENDING 统一转 INTERRUPTED，逐条隔离 | `task_manager.py(_reconcile_orphans)` |
| persistence 加固 | 全操作加锁、原子写(tmp+os.replace)、损坏文件归档不毁史、PermissionError 短重试、list_all() | `persistence.py` |

## 实施中的重大发现
1. **FATAL（已修复）**：挂载 checkpointer 后 langgraph 每 superstep 传 state 副本，旧 `stop()` 改 `_active_states` 的机制彻底失效（stop 信号永远到不了节点，实测挂死）。修复：TaskManager 权威 `_stop_flags` + AgentRuntime `_stopped()` helper 轮询并写回副本。
2. **存量缺陷连带修复**：recursion_limit 默认 25 使 `max_steps>6` 从未真正生效（P1 主拓扑每循环 7 superstep）；现注入 `max_steps*8+20`（子图 ×4+10）。
3. **安全默认**：停在人工确认闸口的任务同步拒绝续跑（pending_confirm 标记随快照落盘）——闸门永不被静默绕过。

## 新增契约
- Settings：`checkpoint_enabled=true` / `checkpoint_dir=""`（空 = <data_dir>/checkpoints）
- REST：`POST /tasks/{id}/resume`（404 不存在 / 409 非 INTERRUPTED·无 checkpoint·已运行·停于闸口）
- SSE：`task_resumed` 事件；trace JSONL append 连续跨 resume

## 本轮文档与测试
- `docs/incremental-arch-p3-resume.md`；README 功能段/API 表/配置表更新
- `backend/tests/`：原 331 + test_checkpointer(6) + test_orphan_reconcile(6) + test_resume(8)
- `.env.example` checkpoint 组；requirements 钉版 `langgraph-checkpoint-sqlite==2.0.11`

---

# Issue #7（langgraph 0.2 → 1.2.x 原地迁移 + 简历化包装）— 交付概览

> 本章追加于 2026-09-12。上面各章是历史交付日志，其中的 351 用例 / `checkpoint-sqlite 2.0.11`
> 都是当时的事实，**不回改**；迁移后的现状以本章为准。

## TL;DR
把整个仓库从 langgraph **0.2.76 原地迁移到 1.2.11**（1.2.x LTS 线）：编排层按 1.x idioms 重写、
显式采用 `durability="sync"`、`checkpoint-sqlite` 升到 3.1.1 闭环两条 CVE、旧 checkpoint 快照声明作废
并有启动检测；随后把 **ruff + mypy 质量门禁入库并进 CI**、README 重写为叙事型。离线基线
**351 → 365 全绿**，真实模型双场景 PASS。迁移的每一步都有实测依据，全部记在
[`docs/migration-langgraph-1x.md`](docs/migration-langgraph-1x.md)。

## 交付状态
- **Phase 0 + Phase 1**：2026-09-11 合入 master（PR #8，merge commit `849f81e`，11 个分阶段 commit
  **未 squash** —— 评估 → 依赖矩阵 → 编排重写 → 新特性 → 评审修复，历史本身是叙事资产）。
- **Phase 2**（本轮）：ruff + mypy 进 CI 并入库最小配置、README 叙事化重写、与 `interview-agent-py`
  互链写分工、本章追加。
- 离线基线：**365 passed**（Phase 1 从 351 加了 14 个用例：+6 旧库守卫、+3 拓扑声明、+2 durability、
  +3 评审后补的守卫）；Phase 2 **用例数不变**（门禁接入不改行为）。
- 关键约束：`resilience.py` / `registry.py` / `nodes.py` 的 `_needs_confirm` 重算逻辑 /
  `conftest.py` 隔离块 **零改动**（Phase 2 的 diff 可逐行验证）。

## 三个阶段落点
| 阶段 | 内容 | 落点 |
|----|------|------|
| Phase 0 | 未跟踪杂物 triage（学习痕迹入库 + 工具态产物 ignore）、`.python-version` 钉解释器、`start.py` 拦截误用根目录 `.venv`、容器不再内置 `.env`、公开仓库描述修正 | `.gitignore` / `learning-records/README.md` / `start.py` / `backend/Dockerfile` |
| Phase 1 | 依赖矩阵迁移、旧快照启动检测与拒绝挂载、编排层 1.x 重写、`durability="sync"` 采用与它撞出的上游崩溃修复 | `requirements.txt` / `task_manager.py` / `graph.py` / `docs/migration-langgraph-1x.md` |
| Phase 2 | ruff + mypy 门禁入库进 CI、README 叙事化、组合分工互链、本章 | `pyproject.toml` / `requirements-dev.txt` / `.github/workflows/ci.yml` / `README.md` / `OVERVIEW.md` |

## Phase 1 的实测结论（推翻了一条继承来的假设）
1. **旧钉版理由是错的**：`checkpoint-sqlite 2.0.11` 的钉版注释写着「3.x 破坏 resume serde 兼容」，
   这条从未被实测。用 `mode=ro` 只读探针对 2.0.11 真实写入的快照（104 行 / 7 thread）做旧栈 vs 新栈
   A/B，结构化 diff 的**全部**差异只有三行版本号 —— 3.1.1 + checkpoint 4.2.0 能无损读回。
2. **旧快照仍然作废，但理由换了**：**读得回 ≠ 跑得续**。resume 要恢复的是 pregel 循环的执行位置
   （`channel_versions` / `versions_seen` / `writes` / 0.2 时代的 `branch:*` 通道），1.x 重写了调度与
   分支语义；跨大版本的执行位置等价性未证明，而 `data/` 是纯运行时数据、可弃。赌错的失败模式是
   静默腐蚀 resume 状态。
3. **破坏面 = 0**：装上新栈、一行生产代码未改即 351 passed。所以「重写」是 idiom 现代化，
   不是兼容性修复。
4. **1.x 新特性选 durability，不选 typed streaming v2**：本项目事件由节点内部 publish 到自建 EventBus，
   与 langgraph 流式输出解耦；改 stream 循环会动 SSE 事件时序与类型，违反「零前端契约变更」。

## 新增硬规则（已写进 AGENTS.md，本轮起长期生效）
1. **依赖矩阵三包同钉**：`langgraph>=1.2,<1.3` + `langgraph-checkpoint==4.2.0` +
   `langgraph-checkpoint-sqlite==3.1.1`。serde（`JsonPlusSerializer`）住在 `langgraph-checkpoint` 里，
   不显式钉它就只是一个 `[4.1,5)` 的浮动窗口 —— 一次普通 `pip install` 就能抬走它，而打标
   （只区分迁移前/后）感知不到 4.x 内部漂移。`langchain*` 不得重新加回显式钉版（全仓 0 import，
   且 `langchain-core<0.3` 与 langgraph 1.x 直接冲突）。
2. **旧快照检测连接必须 `mode=ro`**：对 WAL 库而言，最后一个关闭的**读写**连接会把 `-wal` 折回主库
   并删掉 `-wal`/`-shm` —— 「决定拒绝它」这个动作本身会改写被拒文件，毁掉事后用旧栈取证的现场。
   判定只认 `==`（不认 `>=`）；行数同时数 `checkpoints` 与 `writes`；打标写入必须 try/except 降级为
   拒绝挂载（否则只读目录 / 磁盘满会从「单任务失败」变成「FastAPI 起不来」）。
3. **`durability` 只在挂了 checkpointer 时传，且只经 `TaskManager._invoke_kwargs()` 取参数**：
   langgraph 1.2.11 在**无** checkpointer 时传 `durability="sync"` 会
   `AttributeError: 'SyncPregelLoop' object has no attribute '_put_checkpoint_fut'` —— 恰好就是
   「旧库被拒绝挂载」之后的状态，两个决策会相互撞上。这条不是推理出来的，是被
   `test_new_tasks_still_run_with_legacy_store_present` 抓出来的。

## Phase 2：质量门禁落地（本轮）
### 接入方式
- **`pyproject.toml`（新增）**：ruff `select = ["E4","E7","E9","F","I"]`、`line-length = 120`、
  `target-version = "py311"`；mypy `files = ["backend"]`（生产 + 测试同一把闸）、`python_version = 3.11`、
  `ignore_missing_imports`、**`warn_unused_ignores = true`**、默认档（不加 `--strict` /
  `check_untyped_defs`）。刻意不放 `[build-system]`（本仓库不是可安装包，镜像只 COPY requirements.txt），
  也不放 `[tool.pytest.ini_options]`（隔离靠 conftest 环境变量块；pytest 只在含该 table 时才把
  pyproject 当 inifile —— 已用「加文件前后均 365 passed」验证收集行为未变）。
- **`requirements-dev.txt`（新增）**：`ruff==0.16.7` + `mypy==2.3.1`，**钉死**。linter 的规则集与默认值
  本身会漂移 —— 抬一个小版本就能在零代码改动的前提下把 CI 变红；与 langgraph 三包同钉是同一条理由。
  不并进 `requirements.txt`：运行期镜像不该为 linter 变大。
- **CI `backend-test` job**：新增 `Install quality-gate toolchain` / `Lint (ruff)` / `Typecheck (mypy)`
  三步（放在 pytest 之前，快速失败），`cache-dependency-path` 补上 dev 文件。
  **`guard-protected-files` job 与 `conftest.py` 隔离块一字未动**。

### 基线清理（spec 要求「合入时全净，不留 ignore 债」）
| 工具 | 实测基线 | 处置 | 结果 |
|---|---|---|---|
| ruff（默认全规则集） | 638 处 | **不用默认集**：`UP006/UP035/UP045` 424 处 pep585/604 改写会落到 `resilience.py` / `registry.py`（冻结文件）；`BLE001/S110/S112` 64 处与「失败只降级不中断」语义冲突；`RUF012` 31 处对类级 JSON schema 常量是纯噪音；`RUF100` 与 select 集合耦合，会误删 load-bearing 的 `noqa: E402` | 每条取舍连同数字写进 `pyproject.toml` 注释 |
| ruff（选定 select） | 55 处（36 死 import / 15 import 排序 / 2 重定义 / 2 未用局部变量） | 全部修掉，**零 `per-file-ignores`** | `All checks passed!` |
| mypy（默认档）| 59 处 / 18 文件（87 文件被检查）；开启 `warn_unused_ignores` 后再暴露 2 处陈旧 `type: ignore`，共 61 处 | 修掉 59 处；`registry.py` 的 2 处走**唯一一条 override**（CI 冻结文件，修它必须改冻结签名）| `Success: no issues found in 87 source files` |

### 门禁抓到的真东西（不是纯格式）
1. `services/trace.py`：`_files: Dict[str, object]` 把文件句柄的类型抹平了，`write/flush/close`
   五处全靠运气 —— 改为 `Dict[str, IO[str]]`。
2. `api/routes.py`：`task_trace` 声明 `-> Response` 却会返回 `ApiResponse`（`?format=json` 分支）。
   改成真实类型 `ApiResponse | Response` + **显式 `response_model=None`**：已实测新旧两种写法生成的
   OpenAPI 文档逐字节相同、两个分支的响应也完全相同（FastAPI 原本就因为 `Response` 子类而跳过
   模型推断，显式写出来只是把这份行为钉住）。
3. `core/mcp/client.py`：`_submit` 先调 `_ensure_loop_running()` 再**第二次**读 `self.loop`，
   两次读之间并发 close 能把 loop 换掉。改为由 `_ensure_loop_running()` 单次读并返回校验过的对象。
4. `core/agent/nodes.py:207`：planner 异常分支把 `plan` 赋成 `["Planner error: ..."]`（纯字符串），
   而正常分支是 `List[Dict]` —— 下游 `_plan_confirm` 按 dict 用。**本轮只加注解不改行为**
   （`plan: List[Any]` + 注释说明两种形态），因为那是 `# pragma: no cover - defensive` 分支、
   无测试覆盖，改它属于行为变更，记为后续候选。
5. `tests/test_p2_mcp.py`：`signal.SIGKILL` 在 Windows 上不存在 —— mypy 按当前平台解析，
   本地红、CI（ubuntu）绿。改为 `getattr(signal, "SIGKILL", signal.SIGTERM)`，两个平台运行时语义相同，
   让门禁在 Windows 与 Linux 上**同一个答案**（比在配置里钉 `platform` 更好：后者会掩盖真实的可移植性问题）。
6. 两处陈旧 `type: ignore`（`web_search.py` 的 duckduckgo 导入、`test_p2_mcp.py` 的 method-assign）
   在 `warn_unused_ignores` 下暴露并删除 —— 这个开关的意义就是不让 ignore 沉淀成新债。
7. `nodes.py` 的 `tool._needs_confirm(args)`（MCP per-call 鸭子类型判定）用 `typing.cast` 显式交代给
   mypy：`cast` 在运行时是恒等函数，判定逻辑与异常降级路径未动。**本轮 `nodes.py` 共三处改动**
   （另两处：删一个死 `import uuid`、上面第 4 条的纯注解），`_needs_confirm` 重算块与 `human_confirm_node` 零改动。

## 组合分工（简历化包装，US15）
README 新增第 4 节，与 [`interview-agent-py`](https://github.com/renjianguojinqianfan/interview-agent-py)
显式互链并写分工：本仓库扛 **agent 运行时深度**（图编排 / 断点续跑与检查点语义 / 风险确认闸门 /
熔断重试 / MCP·OpenAPI·Git·插件工具链 / SSE 可观测），数据面刻意轻（sqlite + JSON + 标准库索引，
零外部服务）；对方扛 **业务工程落地**（PostgreSQL + pgvector / Redis / MinIO / async SQLAlchemy /
多阶段 uv 构建 / ADR 序列 / `make verify` 全栈门禁）。两边现在说同一套门禁语言（ruff + mypy + pytest）。
按 spec，对方仓库内的改动不在本次范围（单侧先写）。

## 闸门结果（本轮）
| 闸门 | 结果 |
|---|---|
| `ruff check backend scripts` | `All checks passed!`（零排除） |
| `mypy`（files=backend，生产+测试） | `Success: no issues found in 87 source files` |
| `pytest backend/tests/ -q` | **365 passed**（与接入前逐项相同，用例数不变） |
| `scripts/live_e2e.py --check` | 5/5 PASS（无 Key / 无网络，可写路径全重定向到临时目录） |
| 受保护文件 | `git diff -- backend/core/tools/resilience.py backend/core/tools/registry.py backend/tests/conftest.py` 中，前两者为空、conftest 只有隔离块以下的改动 |
| `guard-protected-files` job | 未触发（本轮未改这两个文件） |

## 新增契约
- **无** API / SSE 事件 / 配置前缀变更；`task_trace` 的 OpenAPI 文档已实测逐字节不变。
- 新增文件：`pyproject.toml`（门禁配置）、`requirements-dev.txt`（门禁工具链）。
- 本地开发多一步：`pip install -r requirements-dev.txt`（README §6 与 AGENTS.md 常用命令已同步）。

## 后续候选（本轮刻意不做，记在这里免得丢）
1. **`registry.py` 的 `get_tool` 返回类型是个谎言**：声明 `-> BaseTool | None`，实返
   `type[BaseTool] | None`（类而不是实例），调用方按实例用会炸。文件被 CI 冻结，本轮用唯一一条
   mypy override 接住并在配置里写明理由 —— 但 override 注释不该成为它的**唯一**记录：
   建议开一张跟踪票，在下一次解冻窗口期修签名并撤掉这条 override。
   **【已解决 · 走 [OVERRIDE] 解冻窗口】** 两处签名均已修正（`get_tool` → `Type[BaseTool] | None`、
   `make_openapi_tool` → `List[OpenAPITool]`；纯注解、零生产调用方、零行为变更），`pyproject.toml`
   里那条唯一 mypy override 已撤除，现零 override 全绿。
2. **`nodes.py:207` planner 异常分支的 `plan` 形态**：降级时赋的是 `["Planner error: ..."]`（纯字符串），
   而正常分支是 `List[Dict]`，下游 `_plan_confirm` 按 dict 用。本轮只加注解不改行为（defensive 分支、
   无测试覆盖）；修它得先决定“计划失败”的形态（改成 dict 计划项，还是让 planner 失败直接走 finish），
   属行为变更，单独开票。
3. **`scripts/live_e2e.py` 的预算与超时**（迁移档 §6 已记，仍未动）：场景 1 的终态预算硬编码 ~60s，
   而真实模型一轮 planner 就要 ~10s；`OpenAICompatibleClient` 未设请求 timeout（SDK 默认 600s），
   单次卡顿会吃掉整个预算。
4. **离线套件会写真实 `data/`**（本轮跑闸门时观察到，非本轮引入）：
   `test_graph.py::test_engineer_smoke_passes` 调 `test_smoke.main()`，而它用的是进程级
   `get_settings()` 单例（指向真实 `data/`）而不是 `tmp_path` —— 每跑一次全量套件就往
   `data/tasks.json` 追加一条 `smoke` 任务（当前已累积 136 条）并重写 `data/artifacts/hello.txt`。
   `data/` 已 gitignore，所以不会污染仓库，但与「离线测试不碰本地状态」的初衷不一致
   （对比：`live_e2e.py --check` 就把可写路径全重定向到临时目录）。本轮四次跑闸门共追加了 4 条。
   修它要把这个 smoke 改成基于 `make_settings(tmp_path)`，属测试行为变更，单独开票。

## 本轮文档
- `README.md`：重写为叙事型（定位与亮点 → 能力 → 编排拓扑 → 迁移章节：动机/矩阵/实测/代价/闸门/安全闭环 →
  组合分工互链 → 质量门禁 → 文档）；顺手校正了几处继承自旧 README 的过期数字
  （REST 14→15、补上遗漏的 artifacts preview 端点、SSE 事件数改为可数的 21、前端组件数按目录写清）。
  **又按“不要一股脑把所有信息写上去”收敛了一轮**：参照 codex（81 行）/ opencode（129）/ pi-mono（115）/
  kimi-cli（177）的体量，从 401 行压到 **160 行 / 9 个标题 / 1 张图**（与 pi-mono 的 115 行/6787 字符
  几乎同一量级）。移出 README 的四块内容都有确定去处，无信息丢失：目录树 → `AGENTS.md` §3；
  REST 表 → `docs/architecture.md` §3.3 + 运行时 Swagger（P2/P3 新端点在各自增量档）；
  15 组配置表 → `.env.example`（逐项带注释）；插件代码示例 → `backend/plugins/example_tool.py`（它本身就是模板）。
  分层图换成一段文字，只留编排拓扑一张 mermaid；迁移章节压成六段摘要（每段一个维度）+ 外链完整实测档。
- `docs/migration-langgraph-1x.md`：§7 「Phase 2 待办」逐条补记已交付（保留原文，因为「当时为什么
  不在迁移分支里做」本身就是阶段划分的一部分）。
- `AGENTS.md`：新增「质量门禁基线全净」硬规则（含两条红线：不准用 `# type: ignore` / `# noqa` 消错、
  `registry` 是唯一 mypy override 且不准再加）；目录表补 `pyproject.toml` / `requirements-dev.txt`；
  常用命令补 lint + typecheck；完成定义与行为边界同步（放宽门禁列入⚠需确认）。
  注：§2 里那三条迁移硬规则（依赖三包同钉 / `mode=ro` 检测 / `durability` 只经 `_invoke_kwargs`）
  是 Phase 1 就已写入的，本轮未动。

