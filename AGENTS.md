# AGENTS.md - langgraph-agent

> 本文件是项目级 agent 手册：**短入口 + 安全边界不变量 + 任务路由 + 验证入口**。
> 不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律；事实与细节按指针找权威源，本文件不存第二份。

## 项目定位与适用范围

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → **一条 loop、计划 / 执行两模式**〔片 S5，代码仍是旧的 planner→executor→tool→reflect〕 → 工具调用 → SSE 实时可视化；深度在运行时（续跑 / 计划-执行两模式 / 熔断 / 工具生态 / 两层注入 / 回滚）。转向决策见 [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)，切法与片号见 `docs/pivot-plan-build.md`。能力见 `README.md`「能力」，交付长史见 `OVERVIEW.md`，设计与 spec 见 `docs/`。

- **本文件面向外部开发 agent**（改这个仓库时读）。
- **产品运行时两层注入**只读 home 层 `~/.agents/AGENTS.md` → 工作区根 `AGENTS.md`（= 沙箱根）；盘根 / 中间层 / 子目录一律不读，`docs/` 不进注入。实现：`backend/core/agent/inject.py`。

## 跨任务安全边界（不变量）

> **本表已随 [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md) 重估**（2026-10-14）：完成验证栈、
> 确认闸门、规划期风险扫描、无人值守评测入口一并退役。带 **〔片 Sx〕** 的行指代码里**还没删**、随该片落地；
> 切法与判据见 `docs/pivot-plan-build.md`。**冻结区已在本片解除**（`resilience.py` / `registry.py` 的 CI 守卫已删）。

| 触发词 | 不变量 | 权威源 |
|---|---|---|
| 危险动作拦截〔片 S2〕 | 危险动作由**设置里的规则表 + 一个钩子**当场拒：命中即不执行，把 `{block, reason}` 当该次调用的结果回给模型；**不弹框、不进图重入**。钩子**不改参数、不 terminate**（与 pi 的有意分歧） | `docs/adr/0008` + `docs/pivot-plan-build.md` §S2 |
| 计划 / 执行两模式〔片 S5〕 | 一个任务 = 一段多轮对话；**一条 loop 一个 `agent` 节点**；**默认执行模式**，用户显式切才走计划（开关**黏性**）；一轮没有工具调用即停下；状态三值（跑着 / 停下 / 出错），**没有「完成」终态**；计划模式工具面 = `explore` 档（只读），执行模式 = 全部已挂载工具 | `docs/adr/0008`（决策）+ `docs/pivot-plan-build.md`（切法） |
| 熔断与工具挂载 | 熔断计数**按运行各记一本账、不跨父子/子任务共享**是定居决策不是 bug；MCP / Git / OpenAPI 工具由 `task_manager` 显式追加（不经 `@register`）。原「冻结区零改动」随 ADR 0008 解除 | `docs/adr/0003`（+ ADR 0008「三笔账」3） |
| 确认闸门〔片 S4 删〕 | 整链（`human_confirm_node` 含 `_needs_confirm` 重算块 / `/confirm` / 五档终态 / 前端确认框）已由 ADR 0008 决定**删除**。**S4 落地前别改它**（免得白改）；S4 之后本行消失 | `docs/pivot-plan-build.md` §S4 |
| 密钥 | 只走环境变量注入；`.env` 的 `llm_api_key` 保持留空；不硬编码、不打印明文，只报 set / not set | `.env.example` 注释 + `backend/config.py`（`llm_api_key` 默认空） |
| 产物登记语义 | 产物 = 本任务（含子任务）**写出的文件**：只有 `registers_artifact=True` 的工具（现役仅 `write` / `edit`）是产物源，`read` / `ls` / `glob` / `grep` 带的 `path` 与动态包装面一律不登记；去重按「任务内 resolved path」收口在 `add_artifact`。读到同文件不新增产物、重写只一条，都是**定居决策不是 bug**，别照旧直觉「修回去」 | `docs/adr/0004`（判据、影响面与重开条件） |
| 质量门禁 | `ruff check backend scripts` 与 `mypy` 必须 0 错；类型错误用真实签名修复（不留 ignore / `noqa`）；mypy 零 override；工具链钉在 `requirements-dev.txt` | `pyproject.toml` 注释（规则集与每条取舍理由）——改配置前先读 |
| 测试隔离 | 离线测试不受本地 `.env` 影响；`conftest.py` 顶部覆盖块（`setdefault` 语义）保持原样；SDK **自读**的 LangSmith / LangChain 开关与 Key 另由同文件的 pop 块摘除（#84）——「离线全绿」不靠开发者 shell 恰好没设变量 | `backend/tests/conftest.py` 顶部两块（代码即权威）+ 复现测试 `test_langsmith_offline_guard.py` |
| 依赖面 | 三包同钉；新依赖需说明理由；抬版走独立 chore + 人工评估（含 Dependabot 自动抬版）；`langchain*` 保持零钉版；uvicorn / starlette 区间不动 | `requirements.txt` 注释（三包理由 / mcp `--no-deps`） |
| 真实模型验证 | 发布前 / 换供应商时，**启服务、在网页端手动跑一遍**（CI 不再跑真模型）；README **不记**跑过的时间与模型 | [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)「三笔账」2 |
| 提交边界 | 副作用可逆或事先说明；提交只含本次意图内的源码与文档（`.env` / `data/` / `frontend/dist` 是本地态）；`push --force` 类默认拒绝 | `.gitignore` + 全局 `~/.agents/AGENTS.md` |
| 发布与合并惯例 | 功能 / 依赖 / 代码走 PR + CI 全绿；docs 小修可直推；依赖类 PR 先人工评估 | `docs/agents/issue-tracker.md`「Release conventions」 |

## 任务路由

| 任务触发 | 先读（权威源） | 完成判据 |
|---|---|---|
| 新票实施（代码） | `docs/agents/workflow.md` | 实施/评审分离：qoder 实施 + `code-review` 自评审 + `neat-freak` 文档收尾（步 3.5，能力面/接口/配置语义变了必须同步文档）+ 提 PR；opencode 独立评 PR；CI 全绿 + 评审通过后由评审侧/用户合并 |
| 后端改动 | `pyproject.toml` | 门禁命令原样跑且 0 错；零 ignore / override |
| 前端改动 | `frontend/package.json` + `ci.yml` 的 frontend-build job | `npm ci` 口径；`npm run typecheck` 与 `npm run build` 通过；**动到对应判据面再加该面的 `check:*`**：SSE 事件面（发布点 / 注册表 / §3.4 表）→ `npm run check:sse`，确认框面（关框 / 切任务 / 五档终态）→ `npm run check:confirm`〔片 S4 删：确认框面随闸门退役，该脚本一并删〕 |
| stop / resume / checkpoint / durability | `docs/incremental-arch-p3-resume.md` + `docs/migration-langgraph-1x.md`（§2 存储守卫、§5 durability） | TaskManager 权威信号（`_stop_flags`）在；三类 409 拒绝逐条仍成立〔片 S4 后「停在闸门」那一类随闸门消失，余两类不变〕 |
| 计划 / 执行两模式 / 状态机 | `docs/adr/0008`（决策）+ `docs/pivot-plan-build.md`（切法与片号） | 一个任务 = 一段多轮对话；**一条 loop 一个 `agent` 节点**；**默认执行模式**、用户显式切才走计划；一轮没有工具调用即停下；状态三值、无「完成」终态。**片 S5 之前代码仍是旧拓扑**，别按新拓扑改旧节点 |
| 依赖升级 | `requirements.txt` 注释 | 本地复现 CI 确切命令后合并 |
| 完成验收语义〔片 S3 删〕 | `docs/adr/0008` + `docs/pivot-plan-build.md` §S3 | 整层已退役：`UNVERIFIED` 终态、`verification` 系列原因值、expectation / rubric、`aux_llm` 裁判、`POST /tasks/{id}/review`、`Task.verification` / `review` 等字段全部随 S3 删除。**状态机改为「跑着 / 停下 / 出错」三值，没有「完成」终态**——「完成」= 人不说了 |
| 开 issue / 定标签 / 领域文档 / 文档归属 | `docs/agents/` 三件套（issue-tracker / triage-labels / domain——「文档归属」表在 domain） | 标签流转按 triage-labels；文档一律落 `docs/` |
| 语义/设计歧义需拍板（如产物定义、面板去留） | 调研 opencode / codex / pi / dsh 等开源 agent 仓库的同类处理（GitHub 实码为证，不靠记忆）；读料可交后台代理跑 `research` skill | 调研产物落 `docs/`（按 domain「文档归属」表，带文件级引用），票面只留结论 + 链接，再拍板 |

## 验证入口与完成证据

```bash
# 离线回归（唯一权威）——解释器一律 .venv311；根目录 .venv 是 3.13 且无依赖，不要用
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q

# 质量门禁（与 CI backend-test 同命令；工具链：pip install -r requirements-dev.txt）
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy

# 真实模型验证（发布前 / 换模型）：启服务、在网页端手动跑一遍（CI 不再跑真模型）
.\.venv311\Scripts\python.exe -m uvicorn backend.main:app --port 8000 --reload
cd frontend; npm run dev

# 前端（cd frontend，Node 22）；CI 同口径 = npm ci → npm run typecheck → npm run check:sse → npm run check:confirm → npm run build
npx tsc --noEmit     # 本地快检，0 错误
npm run check:sse    # SSE 事件词汇表四处对齐（#92）；改事件面必跑，细则见 docs/architecture.md §3.4
npm run check:confirm # 确认框关框 / 切任务 / 五档终态判据（#91）；无测试框架，断言以 plain-Node 脚本落 frontend/scripts/
```

- 提交前钩子（[#114]）：`.venv311\Scripts\python.exe -m pre_commit install`（每台机器一次，含 commit-msg 钩子）；手动全量 `pre_commit run --all-files`；跳过 `SKIP=mypy` 单个或 `--no-verify` 全跳（跳了写清理由）。换位置克隆需同步改配置里的 `.venv311` 绝对路径 entry。钩子清单与实测耗时见 `.pre-commit-config.yaml` 头注。
- 报告完成时区分四种状态：**通过 / 跳过 / 未运行 / 产物验收**；未执行的项写明原因与影响面——`exit 0` ≠ 全验收。
- 既有测试只增不减、不得跳过或放宽；文档不写现役测试计数（数字必腐，以实跑为准）。
- 改接口 / 配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。

## Agent skills

> 本节由 `setup-matt-pocock-skills` 技能生成（2026-09-15），供 mattpocock 工程技能组读取；
> 下面四个 `###` 标题是它们的读取入口。细节改 `docs/agents/*.md` 即可，不必重跑技能。
> 技能组来源 `mattpocock/skills`：本地克隆 `E:\code\mattpocock-skills`（HEAD `6fd9479`，
> 2026-10-07 同步）。上游有更新先 diff 克隆再改本仓文档，别照记忆改。

### Issue tracker

GitHub Issues（`renjianguojinqianfan/langgraph-agent`），走 `gh` CLI；PowerShell 多行 body 用 `--body-file`。See `docs/agents/issue-tracker.md`.

### Triage labels

五个规范角色默认标签，无改名映射（`ready-for-agent` / `wontfix` 已在用）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根 `GLOSSARY.md`（指针式入口，词汇权威在 `backend/core/agent/state.py`，节点名见 `graph.py`）+ `docs/adr/`（0001 / 0002 / 0003 / 0004 / 0005 / 0006 / 0007 / 0008；0005–0007 已随 0008 退役、0002 部分退役）。See `docs/agents/domain.md`.

### Implementation workflow

新票实施分工：qoder 切分支 + `implement` + `code-review` 自评审 + `pr` 写 PR body + `neat-freak` 文档收尾 + 提 PR；合并后 `retro` 复盘；opencode 独立评 PR（不改代码只提返工）；评审侧/用户合并。多票大工程可整 spec 路由 `implement-spec`。See `docs/agents/workflow.md`.

