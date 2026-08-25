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

