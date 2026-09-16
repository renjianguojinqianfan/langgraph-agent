# AGENTS.md - langgraph-agent

> 本文件是 `langgraph-agent` 项目根目录的 Agent 操作手册 / 核心指令集。不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律，只写本目录特有的东西。

## 1. 快照
基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台，前后端一体：自然语言下发任务 → Agent 自主规划（planner→executor→tool→reflect 循环）→ 调用工具完成多步任务 → SSE 实时可视化。经 v1 + P0（对齐 HelloAgents/DeepAgent）+ P1（六项能力）+ P2（MCP/Git）+ P3（断点续跑，Issue #4）+ langgraph 0.2→1.2.x 原地迁移（Issue #7）+ P0-A（文件精读编辑工具 read/edit/glob/grep，roadmap-pawbench）+ P0-C（headless 无人值守入口，backend/headless.py）+ P0-B（完成验证：reflect 落地确定性自检——S1 产物完整性 + S2 全程失败闸 + 有界回环降级，Issue #25）+ P1-A′（上下文注入层：两层 AGENTS.md + skills 清单 + 环境事实进 system，任务级缓存、永不裁、不计入压缩预算，spec `docs/specs/p1-a-prime-context-injection.md`）多轮迭代，**480 个离线测试全绿 + ruff/mypy 基线全净** + 真实 LLM（qwen3.6-plus）端到端与断点续跑双场景验证通过。

## 2. 硬规则（改前必读）
- **测试环境隔离**：离线测试必须不受本地 `.env` 影响——`backend/tests/conftest.py` 顶部用环境变量覆盖（`LLM_BASE_URL=""` / `USE_MOCK_LLM=true` / `AUX_LLM_ENABLED=false` / `AUTH_ENABLED=false` / `OPENAPI_ENABLED=false` / `CHECKPOINT_ENABLED=false` / `CONTEXT_INJECT_ENABLED=false`）。**改 conftest 时勿破坏这段隔离**，否则本地 live 配置会污染全部离线用例。
- **P0 死循环修复勿动**：`backend/core/agent/nodes.py` 的 `human_confirm_node` 中 `_needs_confirm` 重算逻辑（确认/拒绝后重算是否仍有待确认项）是 P0 修复的死循环根因，**一字不动**；风险确认复用该机制，新增确认逻辑必须走 `_confirmed_ids/_rejected_ids` 流程。（P3 在该节点 else 分支追加了 stop-forced 的 `pending_confirm` 标记，位于重算块之前，属于 Issue #4 行为，保留。）
- **熔断层零改动**：`backend/core/tools/resilience.py`（CircuitBreaker/with_retry/ToolExecutor）与 `backend/core/tools/registry.py` 是 P0/P2 的多轮约束对象，改动需极谨慎——MCP/Git/OpenAPI 工具一律由 `task_manager.py` 显式追加（`_load_mcp_tools`/`_load_git_tools`），**不经 @register**。（CI 已由 `guard-protected-files` job 机械化拦截这两个文件的改动，逃生舱：PR/commit 标题含 `[OVERRIDE]`。）
- **真实 LLM 验证**：离线测试只证明确定性；发布前/换供应商用 `scripts/live_e2e.py` 跑真实模型（`LLM_API_KEY="$DASHSCOPE_API_KEY" python scripts/live_e2e.py`，含场景 1 冒烟 + 场景 2 断点续跑）。Key 只走环境变量，**绝不硬编码、绝不打印明文**；`.env` 的 `llm_api_key` 留空靠环境变量注入。
- **解释器**：pytest 一律用 `.venv311/Scripts/python.exe`（Python 3.11.15，已装依赖；根目录 `.venv` 是 3.13 无依赖，不要用）。
- **质量门禁基线全净**（Issue #7 Phase 2）：`ruff check backend scripts`（select=`E4,E7,E9,F,I`）与 `mypy`（`files=["backend"]`，生产+测试同一把闸）都必须 0 错。配置、以及每条「刻意不启用」的规则族连同实测数字都写在 `pyproject.toml` 注释里（例：UP 会改到冻结文件、BLE001/S110 与「失败只降级不中断」语义冲突）——**改配置前先读那段**。工具链在 `requirements-dev.txt` **钉死**（linter 版本漂移能在零代码改动时把 CI 变红）。两条红线：**不准用 `# type: ignore` / `# noqa` 消错**（`warn_unused_ignores = true` 会把陈旧 ignore 变成新错，且 ignore 就是 spec 禁止的债）；**mypy 现零 override**——`backend.core.tools.registry` 那 2 处返回类型谎言（`get_tool` 声明 `BaseTool | None` 实返 `Type[BaseTool] | None`、`make_openapi_tool` 声明 `List[BaseTool]` 实返 `List[OpenAPITool]`）已走解冻窗口（`[OVERRIDE]` 破门）修正签名、Phase 2 那条唯一 override 已从 `pyproject.toml` 撤除，**不准新增任何 override**。
- **PR 审查与合并态度**（单人开发/学习自用，审查力度匹配改动风险，不搞一刀切仪式）：① **分级**——功能/依赖/代码改动走 feature 分支 + PR + CI 全绿才合（护「master 全绿」简历主张）；docs/收尾小修因 `ci.yml` 的 `paths-ignore: docs/**、*.md` 不进 CI、不影响全绿，可直接 commit+push master、免 PR/admin-merge 仪式。② **实证优先、不盲信状态标签**——合并前本地复现 CI 的**确切命令**（前端 `npm ci` 而非 `npm install`）；`BLOCKED`/无 checks/CI 红 ≠ 可绕过或强合，先深挖根因、必要时弃自动分支手动接管（PR #6→#15：dependabot rebase 漏升配套 plugin-react 致 `npm ci` ERESOLVE，手动配套 bump 才绿）。③ dependabot 自动抬版勿直接合（见「依赖矩阵」），跨 major 升级警惕配套 peer 缺失；合并方式/凭据/删分支细节见记忆（`[OVERRIDE]` PR 用 squash 免 guard 变红、OAuth 回落、`--delete-branch` 需 ls-remote 核对）。
- **端口**：后端 8000、前端 dev 5173。勿与 `trae` 项目同时跑（同端口冲突）。

### P3 断点续跑专项约束（Issue #4 + Issue #7 迁移）
- **checkpoint 副本语义**：挂载 checkpointer 后 langgraph 每 superstep 向节点传 state **副本**——任何跨节点通信不得再依赖共享字典引用，必须走 TaskManager 权威信号（`_stop_flags` / `is_stop_flagged()`），nodes 侧统一经 `_stopped(state)` helper 轮询并写回副本。
- **resume 拒绝语义不可放松**：非 INTERRUPTED、无 checkpoint、停在确认闸口（pending_confirm 标记）三类一律 409 同步拒绝——闸门永不被静默绕过。
- **依赖矩阵**：`langgraph>=1.2,<1.3`（1.2.x LTS 线）+ `langgraph-checkpoint==4.2.0` + `langgraph-checkpoint-sqlite==3.1.1`——三个包一起钉，因为它们合起来就是 resume 契约的存储面（serde `JsonPlusSerializer` 住在 `langgraph-checkpoint` 里，不钉它就只是一个 [4.1,5) 的浮动窗口，一次普通 `pip install` 就能抬走它且打标感知不到）。抬版需人评估放行，Dependabot 自动抬版勿直接合。`langchain*` 不得重新加回显式钉版（全仓 0 import，且 `langchain-core<0.3` 与 langgraph 1.x 直接冲突）；uvicorn/starlette 区间不动。
- **旧快照作废 + 启动检测**（Issue #7）：上游没留版本痕迹（2.0.11 与 3.1.1 建表 SQL 逐字符相同），所以标记由本仓库自己打：挂载成功即 `PRAGMA user_version = CHECKPOINT_FORMAT_VERSION`。判定只认 `==`（不认 `>=`：更新的 build 写的库本 build 同样证明不了）；行数同时数 `checkpoints` 与 `writes`。拒绝时 `_checkpointer` 留 None + ERROR 告警，服务照常起、新任务照常跑，resume 落回「no checkpoint → 409」；不做自动迁移。两条红线：检测连接必须 `mode=ro`（读写连接关闭时会把 WAL 折回主库并删掉 `-wal`/`-shm`，“决定拒绝”就会改写被拒文件）；打标写入必须 try/except 降级为拒绝挂载（否则只读目录/磁盘满会从“单任务失败”变成“FastAPI 起不来”）。日志文案按 kind 分支：`legacy`/`newer` 叫人把文件移走，`unreadable`/不可写 叫人去修权限与完整性、**不要**删快照。逻辑在 `task_manager._inspect_checkpoint_store` / `_mount_checkpointer`，实测依据见 `docs/migration-langgraph-1x.md` §2。
- **durability 只在挂了 checkpointer 时传**：`_DURABILITY = "sync"`（1.x 新特性，保证 stop() 后快照已落盘），但 langgraph 1.2.11 在**无 checkpointer** 时传 `durability="sync"` 会 `AttributeError: 'SyncPregelLoop' object has no attribute '_put_checkpoint_fut'` —— 恰好就是拒绝挂载后的状态。因此 invoke 一律经 `TaskManager._invoke_kwargs()` 取参数，**不要**直接写 `durability=`；子任务图（无 checkpointer）同理不传。

## 3. 目录结构
| 路径 | 内容 |
|---|---|
| `pyproject.toml` | 质量门禁配置（ruff + mypy），含每条「刻意不启用」的理由与实测数字。**不放** `[build-system]`（本仓库不是可安装包）也不放 `[tool.pytest.ini_options]`（隔离靠 conftest 环境变量块）|
| `requirements.txt` / `requirements-dev.txt` | 运行期依赖（langgraph 三包同钉，理由在文件注释）/ 门禁工具链（ruff+mypy 钉死；不进镜像，Dockerfile 只 COPY 前者）|
| `backend/config.py` | Settings（17 组配置前缀：llm/context/context_inject/tool/plugins/trace/risk/subagent/kb/aux_llm/auth/openapi/mcp/git/checkpoint/sandbox/verify）|
| `backend/core/agent/` | 编排：state / nodes（planner/executor/tool/reflect/risk_scan/subagent_split/human_confirm）/ graph（mode=main\|subtask）/ context（压缩）/ inject（P1-A′ 上下文注入：两层 AGENTS.md + skills 清单 + 环境事实，纯函数组）/ risk（EHRB）/ subagent |
| `backend/core/tools/` | BaseTool 规范 + 18+ 工具：web_search/file_io/read/edit/glob/grep/code_exec/http_request/memory_search/kb_query/spawn_subagent/git_*(7)/McpTool/OpenAPITool + resilience(熔断) + registry(插件发现)。P0-A 新增 read/edit/glob/grep 四个六件套式独立文件工具（沙箱内精读/精确编辑/递归匹配/内容检索），file_io 暂留待退役 |
| `backend/core/llm/` | LLMClient 抽象 + OpenAI 兼容工厂 + Mock/Aux |
| `backend/core/kb/` | KnowledgeBase（标准库关键词索引，离线可用）|
| `backend/core/mcp/` | McpClientManager（stdio 传输，每 server 一线程+事件循环）|
| `backend/services/` | event_bus / trace(JSONL) / persistence / task_manager / auth(hmac) |
| `backend/api/` | routes（15 REST）/ sse / schemas（`/health` 另在 `main.py`）|
| `backend/headless.py` | P0-C 无人值守单发入口（`python -m backend.headless -p "<题>" --dir <工作目录> --auto-approve --output json`）：复用 TaskManager 跑一题、产物落 --dir、trace 落盘、JSON 结果+退出码；评测台驱动本 agent 的统一命令；`--check` 为离线冒烟 |
| `backend/plugins/` | 插件目录（自动发现 BaseTool，example_tool.py）|
| `backend/tests/` | **41 个 `test_*.py`（+ conftest / mcp_echo_server / __init__ 共 44 个 .py）/ 480 用例**（含 test_qa_* 独立补充；test_checkpointer/test_resume/test_orphan_reconcile 为 P3；test_file_tools 为 P0-A；test_headless 为 P0-C；test_verify 为 P0-B；test_context_injection 为 P1-A′）|
| `frontend/` | React 三栏 UI：`components/` 14 组件（TaskPanel/TraceTab/RiskBanner/SubtaskList/KbPanel …）+ `pages/` 2 页面（LoginPage/TaskView）|
| `docs/` | prd / architecture / 增量 PRD+架构（p0/p1/p2/p3-resume）/ migration-langgraph-1x（0.2→1.2.x 迁移评估与实测）/ capability-first-principles / agent-comparison-report / roadmap-pawbench / pawbench-harness-interface |
| `docs/adr/` | **决策记录**（`NNNN-英文-kebab-case.md`）。现有 0001 = 简历项目定位与 langgraph 原地迁移决策（grilling D1–D9，原 `.qoder/specs/…_grilling总结.md`）|
| `docs/specs/` | **spec / 实施计划档案**。现有 langgraph-1x-migration / p0-b-completion-verification / second-tier-ci-guard-and-smoke（前三份自 `.qoder/specs/` 迁入）+ p1-a-prime-context-injection（本轮新建）|
| `docs/agents/` | mattpocock 技能组配置：issue-tracker / triage-labels / domain（由 `setup-matt-pocock-skills` 生成，本文件末尾 `## Agent skills` 段是其入口）|
| `.agents/skills/` | 千问官方 skills（model-selector/ops-auth/usage）。集成路径：references 由 `scripts/live_skill_test.py` 复制到 `data/kb/qianwen-skills/` 并重建索引 → 真实模型任务中经 `kb_query`/`memory_search` 工具检索；该脚本同时验证"KB 命中 + 答案给出具体模型"全链路 |
| `scripts/` | live_e2e.py（真实 LLM 验证，`--check` 为无 Key 离线冒烟）/ live_skill_test.py（skills→KB→真实模型）|
| `data/` | 运行时生成（tasks.json/traces/kb/artifacts），不入库 |

### 文档归属（2026-09-15 起，硬约定）

决策类 / 计划类 / spec 类文档**一律落 `docs/`**，禁写 `.qoder/`、`.trae/`、`.mimosa/` 等各家 agent 专用目录——那些目录整体不入库（见 `.gitignore`），换机器或换家 agent 就丢，无法当跳会话交接的权威来源。

| 类型 | 去处 | 命名 |
|---|---|---|
| 决策记录（grilling 定居、定位/选型结论）| `docs/adr/` | `NNNN-英文-kebab-case.md`（4 位递增编号）|
| spec / 实施方案 / 任务计划 | `docs/specs/` | `英文-kebab-case.md` |
| 调研 / 对标 / 路线图 / 迁移评估 | `docs/` 根 | 同上（沿用既有惯例）|
| 技能组配置 | `docs/agents/` | 由 setup 技能生成 |
| **记忆类（不迁）** | 留原地：`.workbuddy/memory/`、teach 的 `MISSION.md`/`NOTES.md`/`RESOURCES.md`/`learning-records/`/`lessons/` | 各家 agent 自己消费；已入库的学习痕迹类保持入库，未入库的保持 ignore |

文件名用英文 kebab-case（与 `docs/` 现有全部文件一致），内容中文照旧。迁移时用 `git mv` 保历史（`git log --follow` 可追），并顺手改掉旧路径引用。

## 4. 常用命令
```bash
# 后端（要求已建 .venv311 并装依赖）
.\.venv311\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000

# 全部离线测试（唯一权威回归）
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q

# 质量门禁（需先 pip install -r requirements-dev.txt；与 CI backend-test job 同命令）
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy          # 不传路径：files 由 pyproject.toml 给出

# 真实 LLM 端到端（发布前/换供应商）
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_e2e.py

# 离线冒烟（无 Key/无网络，验证脚本依赖链可装配；已接入 CI）
.\.venv311\Scripts\python.exe scripts/live_e2e.py --check

# P0-C headless 单发（无人值守跑一题；--auto-approve 旁路确认闸门=评测态）
.\.venv311\Scripts\python.exe -m backend.headless -p "<任务>" --dir ./out --auto-approve --output json
.\.venv311\Scripts\python.exe -m backend.headless --check   # headless 入口离线冒烟（已接入 CI）

# skills 知识库联测（skills references → KB → 真实模型）
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_skill_test.py

# 前端（先 cd frontend，Node 22）
npm run dev          # 5173，proxy /api 到 8000
npx tsc --noEmit     # 类型检查 0 错误
```

## 5. 完成定义
- `ruff check backend scripts` 与 `mypy` 均 0 错（基线全净，不许靠 ignore/override 绕过）。
- 后端 `.venv311 python -m pytest backend/tests/ -q` 全绿（480）；改前端时 `npx tsc --noEmit` 0 错误。
- 改动跑通真实模型冒烟（有 Key 时）：`scripts/live_e2e.py` PASS。
- 改接口/配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。
- 新依赖需说明理由；**避免升级 uvicorn/starlette**（mcp 依赖冲突教训：用 `--no-deps` 装 mcp）。
- 新副作用（写库/建文件/起服务）可逆或已口述说明。

## 6. 行为边界
- ✅ 允：改 `backend/`、`frontend/`、`tests/`、`docs/`、`scripts/`；修 bug 不加多余特性。
- ⚠️ 需确认：改 `_needs_confirm` 重算、改 `resilience.py`/`registry.py`、改 conftest 隔离、加第三方依赖、动端口、放宽质量门禁（加 ignore / 扩 mypy override / 改 ruff select / 抬 linter 版本）。
- ⛔ 禁止：提交 `.env`/`data/`/`frontend/dist` 里的密钥或生成物；绕过确认直接执行危险工具（code_exec/git_commit/http 写类/MCP 写类）；硬编码密钥；`git push --force` 类危险命令（黑名单默认拒绝）。
- 📌 P0-C 例外说明：`backend/headless.py --auto-approve` 经 `TaskManager(auto_approve=True)` **实例级**置 `confirm_enabled=False` 旁路确认闸门——**仅 headless 入口设置、FastAPI 服务端路径（`main.py` lifespan）永不传该参**，故非「全局关闸」开关；这是评测态刻意降级（≠生产态），`nodes.py` 的 `_needs_confirm` 重算块一字未动。

## Agent skills

> 本节由 `setup-matt-pocock-skills` 技能生成（2026-09-15），供 mattpocock 工程技能组
> （`triage` / `to-spec` / `to-tickets` / `wayfinder` / `domain-modeling` / `implement` …）读取。
> 细节改 `docs/agents/*.md` 即可，不必重跑技能——只有换 issue tracker 或推倒重来才需要重跑。

### Issue tracker

GitHub Issues（`renjianguojinqianfan/langgraph-agent`），一律走 `gh` CLI；Windows PowerShell 下多行 body 用 `--body-file`，不用 heredoc。See `docs/agents/issue-tracker.md`.

### Triage labels

沿用五个规范角色的默认标签字符串（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`），无改名映射；其中 `ready-for-agent` 与 `wontfix` 仓库里早已在用。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录一份 `CONTEXT.md` + 一份 `docs/adr/`（两者目前都还没建，由 `/domain-modeling` 惰性创建）；在其就位前，事实词汇表是本文 §2–§3 与 `backend/core/agent/state.py`，冻结决策等价物是本文 §2 硬规则。See `docs/agents/domain.md`.
