# LangGraph 自主任务 Agent

一个基于 **LangGraph (Python)** 编排的「自主任务 Agent」平台：用户用自然语言下发任务，
Agent 自主规划、循环调用工具（联网检索 / 文件读写 / 代码执行 / HTTP API / Git / MCP 外部工具 / 知识库）
完成多步任务，并通过前后端一体的可视化界面（三栏布局 + SSE 实时事件流 + Trace 回放）展示全程。

**331 个离线测试全绿** · 真实 LLM（OpenAI 兼容，已验证千问 qwen3.6-plus）端到端跑通。

> 技术栈：Python 3.10+ · LangGraph · FastAPI · OpenAI 兼容 LLM 抽象层（可 Mock）·
> React 18 · Vite · TypeScript · Tailwind CSS · Zustand · MCP SDK

---

## 1. 功能特性

### 核心编排
- **自主循环**：LangGraph `StateGraph` 的 planner → executor → tool → reflect 条件边循环，直到产出最终答案
- **双模式图**：`mode="main"`（完整拓扑）/ `mode="subtask"`（简化图，子任务防递归）
- **人工介入**：危险工具 / 高危计划执行前 `human_confirm` 中断，可批准或拒绝
- **停止控制**：任意时刻停止任务，≤2s 生效
- **上下文压缩**：历史超阈值（默认 8000 tokens）自动截断 / 可选 LLM 摘要，控制 Token 上限

### 安全与可靠性
- **规划期风险扫描（EHRB）**：五类危险词表（删除/破坏/财务/隐私/通信），高危操作强制确认
- **工具熔断 + 重试退避**：连续失败自动短路（3 次 / 冷却 30s / half-open），指数退避重试
- **沙箱**：文件白名单防逃逸、代码执行受限 subprocess、Git 命令参数化防注入 + 危险命令黑名单
- **鉴权**：hmac token 签发（默认关闭，本地 demo 便利）

### 工具集（BaseTool 统一接口，可扩展）
| 类别 | 工具 |
|------|------|
| 基础 | `web_search`（DuckDuckGo/SerpAPI）、`file_io`、`code_exec`、`http_request` |
| 知识 | `memory_search` / `kb_query`（跨会话 RAG，离线关键词检索可用）|
| 协作 | `spawn_subagent`（隔离子任务，线程池并行）|
| Git | `git_status` / `git_diff` / `git_commit` / `git_log` / `git_branch` / `git_checkout` / `git_init` |
| 外部 | **MCP 客户端**（stdio 连接外部 server，工具自动注册 `mcp__server__tool`）、**OpenAPI**（spec 一键生成工具）、**插件目录**（`backend/plugins/` 自动发现）|

### 可观测与工程化
- **SSE 实时事件流**：15+ 种事件（plan_update / tool_call / tool_result / risk_report / subtask_* / tool_circuit_open / context_compressed ...）
- **Trace 回放**：EventBus 事件落盘 JSONL，前端 Trace Tab 时间线 + 导出 `.jsonl`
- **辅助模型分工**：可选 `aux_llm`（风险语义分析 / 摘要降本），默认关闭零额外调用
- **统一响应信封** `{code, data, message}`；配置全部走 `.env`（14 组前缀）

---

## 2. 目录结构

```
langgraph-agent/
├── AGENTS.md                 # 项目 Agent 操作手册（硬规则/边界）
├── README.md / .env.example / requirements.txt / start.py / docker-compose.yml
├── backend/
│   ├── main.py               # 应用入口 / lifespan（MCP 优雅关闭）/ CORS
│   ├── config.py             # pydantic-settings（14 组配置前缀）
│   ├── api/                  # routes（14 REST）/ sse / schemas
│   ├── core/
│   │   ├── llm/              # LLMClient 抽象 + OpenAI 兼容工厂 + Mock/Aux
│   │   ├── tools/            # BaseTool + 14+ 工具 + resilience(熔断) + registry(插件)
│   │   ├── agent/            # state / nodes / graph / context(压缩) / risk / subagent
│   │   ├── kb/               # KnowledgeBase（标准库关键词索引，离线可用）
│   │   └── mcp/              # McpClientManager（stdio，每 server 一线程）
│   ├── services/             # event_bus / trace / persistence / task_manager / auth
│   ├── plugins/              # 插件目录（example_tool.py）
│   └── tests/                # 37 文件 / 331 用例（含 test_qa_* 独立补充）
├── frontend/                 # React 三栏 UI（14 组件：TaskPanel/TraceTab/RiskBanner/...）
├── docs/                     # PRD / 架构 / 增量设计（p0/p1/p2）
├── .agents/skills/           # 千问官方 skills（供知识入库 / 模型选型参考）
└── scripts/                  # live_e2e.py（真实 LLM 验证）/ live_skill_test.py
```

---

## 3. 快速开始

### 3.1 后端

```bash
cd langgraph-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 填 llm_api_key，或设 use_mock_llm=true 离线跑
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

> 不填 Key 也能跑：`use_mock_llm=true` 启用离线 Mock LLM（无需网络/Key）。
> 真实模型示例（千问 DashScope）：`llm_base_url=https://dashscope.aliyuncs.com/compatible-mode/v1` + `llm_model=qwen3.6-plus`。

### 3.2 前端

```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173（/api 代理到 :8000）
```

### 3.3 一键启动

```bash
python start.py --mock             # 前后端 + 离线 Mock
python start.py                    # 前后端 + 真实 Key（需 .env）
```

---

## 4. 测试与验证

```bash
# 全量离线测试（331 用例，MockLLM，无需 Key/网络）
.venv\Scripts\python.exe -m pytest backend/tests/ -q

# 真实 LLM 端到端（发布前 / 换供应商验证，需 .env 配置真实模型）
LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_e2e.py

# skills 知识库联测（skills references → KB → 真实模型检索推荐）
LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_skill_test.py
```

---

## 5. 核心 API（`/api` 前缀，统一信封 `{code,data,message}`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 下发任务 `{input}` → `{task_id}` |
| GET  | `/api/tasks` | 历史任务列表 |
| GET  | `/api/tasks/{id}` | 任务完整状态 |
| POST | `/api/tasks/{id}/stop` | 停止任务（≤2s） |
| POST | `/api/tasks/{id}/resume` | 从断点续跑 INTERRUPTED 任务（Issue #4） |
| GET  | `/api/tasks/{id}/events` | **SSE** 步骤事件流 |
| GET  | `/api/tasks/{id}/trace` | Trace 回放（NDJSON / ?format=json）|
| GET  | `/api/tasks/{id}/artifacts/{aid}` | 下载 / 预览产物 |
| POST | `/api/tasks/{id}/confirm` | 人工确认 |
| POST | `/api/auth/token` | 登录签发（默认关闭）|
| GET/POST/DELETE | `/api/kb`... | 知识库管理 / 重建 / 删除 |
| GET  | `/api/mcp/servers` | MCP 服务器状态 |
| GET  | `/health` | 健康检查 |

---

## 6. 配置（`.env`，14 组前缀）

| 前缀 | 用途 | 示例默认 |
|------|------|---------|
| `llm_*` | 主模型 | provider=openai / model=gpt-4o-mini |
| `aux_llm_*` | 辅助模型（默认关）| enabled=false |
| `context_*` | 上下文压缩 | token_budget=8000 / keep_recent=10 |
| `tool_*` | 熔断重试 | failure_threshold=3 / cooldown=30s |
| `risk_*` | 风险扫描 | scan_enabled=true / policy=confirm |
| `subagent_*` | 子 Agent | enabled=true / max_concurrency=2 |
| `kb_*` | 知识库 | dir=data/kb / top_k=5 |
| `mcp_*` | MCP 客户端 | servers=[] / timeout=30s |
| `git_*` | Git 工具 | enabled=true / repo_dir=data/repos |
| `checkpoint_*` | 断点续跑存储 | enabled=true / dir=data/checkpoints |
| `openapi_*` | OpenAPI 工具 | enabled=false |
| `auth_*` | 鉴权 | enabled=false / token_ttl=86400 |
| `plugins_*` | 插件 | dir=backend/plugins / autoload=true |
| `trace_*` | Trace 落盘 | enabled=true / dir=data/traces |
| `sandbox_*` | 沙箱 | timeout=30s |

完整样板见 [.env.example](.env.example)。

---

## 7. 扩展

**新工具（插件目录自动发现）**：在 `backend/plugins/` 放一个 `BaseTool` 子类即可，无需改内核：

```python
from backend.core.tools.base import BaseTool, ToolResult, register

@register
class MyTool(BaseTool):
    name = "my_tool"
    description = "What it does."
    args_schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data={"ok": kwargs.get("x")})
```

**接入 MCP 服务器**：在 `.env` 的 `mcp_servers` 里配置 stdio server（如 `npx` 启动的 MCP server），启动时工具自动注册。

**OpenAPI 一键成工具**：配置 `openapi_spec_path` / `openapi_spec_url`，每个 operation 自动生成一个 BaseTool。

**断点续跑（Issue #4）**：任务执行内核挂载 LangGraph `SqliteSaver` 检查点，每次循环步落盘完整 AgentState。被停止（INTERRUPTED）的任务可经 `POST /api/tasks/{id}/resume` 从最近检查点继续执行——消息历史、计划、已批准/拒绝的确认记录全部保留；进程崩溃遗留的孤儿任务在启动时自动对账为可恢复。两个安全默认：停在人工确认闸口的任务不可续跑（闸门永不被静默绕过）；CONFIRMED/FAILED 终态拒绝恢复。详见 [Issue #4 spec](https://github.com/renjianguojinqianfan/langgraph-agent/issues/4) 与 [增量架构](docs/incremental-arch-p3-resume.md)。

---

## 8. Docker（可选）

```bash
cp .env.example .env   # 填入真实 Key
docker compose up --build
```

---

## 9. 文档

- [PRD](docs/prd.md) · [架构设计](docs/architecture.md)（含类图/时序图）
- [P0 增量（对齐 HelloAgents/DeepAgent 四件套）](docs/incremental-prd-p0.md) · [P0 架构](docs/incremental-arch-p0.md)
- [P1 增量（风险扫描/子Agent/RAG/辅助模型/鉴权/OpenAPI）](docs/incremental-prd-p1.md) · [P1 架构](docs/incremental-arch-p1.md)
- [P2 增量（MCP 客户端 / Git 工具）](docs/incremental-prd-p2.md) · [P2 架构](docs/incremental-arch-p2.md)
- [P3 增量（断点续跑 / LangGraph checkpointer）](docs/incremental-arch-p3-resume.md)
- [真实 LLM 接入指南](scripts/LIVE_E2E.md)
- 项目 Agent 操作手册：[AGENTS.md](AGENTS.md)

## 10. License

MIT
