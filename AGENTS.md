# AGENTS.md - langgraph-agent

> 本文件是项目级 agent 手册：**短入口 + 安全边界不变量 + 任务路由 + 验证入口**。
> 不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律；事实与细节按指针找权威源，本文件不存第二份。

## 项目定位与适用范围

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → **一条 loop、计划 / 执行两模式** → 工具调用 → SSE 实时可视化；深度在运行时（续跑 / 计划-执行两模式 / 工具生态 / 两层注入 / 工作区文件）。转向决策见 [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)，切法与片号见 `docs/archive/pivot-plan-build.md`。能力见 `README.md`「能力」，交付长史见 `docs/archive/OVERVIEW.md`，设计与 spec 见 `docs/`。

- **最简 agent 一章（pi 式工具面、计划文件、一条命令启动，2026-10-22 已交付）**：[ADR 0009](docs/adr/0009-minimal-agent-chapter.md)，切片 C1–C7 见 `docs/capability-registry-rescope.md` §0.9。现役以下面的「跨任务安全边界」表 + 代码为准。
- **日常形态补充（启动目录即工作区、共用一个后台、多套模型配置，2026-10-22 已交付）**：[ADR 0010](docs/adr/0010-launch-dir-workspace-and-model-profiles.md)；实施记录见 `docs/specs/qianfan-daily-form-plan.md`（spec #166，切片 PR #167、#176–#187）。现役以下表 + 代码为准。
- **本文件面向外部开发 agent**（改这个仓库时读）。
- **产品运行时两层注入**只读 home 层 `~/.agents/AGENTS.md` → 任务目录 `AGENTS.md`（`Task.workspace`；旧任务为 `data/artifacts`）；盘根 / 中间层 / 子目录一律不读，`docs/` 不进注入。实现：`backend/core/agent/inject.py` + `backend/core/runtime_context.py`。

## 跨任务安全边界（不变量）

> **本表已随 [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md) 重估**（2026-10-14）：完成验证栈、
> 确认闸门、规划期风险扫描、无人值守评测入口已退役；现役形态见本表与 `README.md`「核心模型」。

| 触发词 | 不变量 | 权威源 |
|---|---|---|
| 危险动作拦截 | 危险动作由**设置里的规则表 + 一个钩子**当场拒：命中即不执行，把 `{block, reason}` 当该次调用的结果回给模型；**不弹框、不进图重入**。钩子**不改参数、不 terminate**（与 pi 的有意分歧） | `docs/adr/0008` + `backend/core/agent/policy.py`（规则表）与 `nodes.py::_handle_tool_calls`（钩子所在）+ [docs/security.md](docs/security.md)（起步集与威胁模型） |
| 计划 / 执行两模式 | 一个任务 = 一段多轮对话（同一 `thread_id` / checkpoint，停下后 `POST /api/tasks/{id}/messages` 续说）；**一条 loop 一个 `agent` 节点**；**默认执行模式**，用户显式切才走计划（任务上的开关，**黏性**）；一轮的最后一次 `agent` 调用没有工具调用即停下；状态三值（跑着 / 停下 / 出错），**没有「完成」终态**；计划模式工具面 = `explore` 档（只读、派发时也拒绝档外工具），执行模式 = 全部已挂载工具；计划 = 任务目录里的 `.qianfan/plans/<task_id>.md`（唯一来源）；停下时可整份改（`PUT /api/tasks/{id}/plan` 写文件），改动随后续消息生效 | `docs/adr/0008`（决策 1–8）+ `backend/core/agent/nodes.py`（`agent` 节点）+ `backend/services/plan_file.py`（计划文件）+ `backend/services/task_manager.py`（续说 / 模式 / 计划） |
| 工具挂载与失败回传 | MCP 工具由 `task_manager` 显式追加（不经 `@register`）；工具每次调用**只执行一次**，失败原因原样回给模型——**无自动重试、无熔断**（ADR 0003 已退役） | `backend/services/task_manager.py`（MCP 追加）+ `backend/core/agent/nodes.py::tool_node` |
| 密钥 | Key 只存在仓库外的 `~/.qianfan/credentials.json`（界面粘贴）或配置里写的环境变量（如 `LLM_API_KEY` / `DASHSCOPE_API_KEY`）；不进仓库、日志、trace、SSE；接口只回「已设 / 未设」与来源（粘贴 / 环境变量 / 无）；`.env` 的 `llm_api_key` 保持留空；不硬编码、不打印明文 | `backend/services/model_store.py` + `.env.example` 注释 + [docs/security.md](docs/security.md)（明文决策 / 轮换 / 暴露面） |
| 质量门禁 | `ruff check backend scripts` 与 `mypy` 必须 0 错；类型错误用真实签名修复（不留 ignore / `noqa`）；mypy 零 override；工具链钉在 `requirements-dev.txt` | `pyproject.toml` 注释（规则集与每条取舍理由）——改配置前先读 |
| 测试隔离 | 离线测试不受本地 `.env` 影响；`conftest.py` 顶部覆盖块（`setdefault` 语义）保持原样；SDK **自读**的 LangSmith / LangChain 开关与 Key 另由同文件的 pop 块摘除（#84）——「离线全绿」不靠开发者 shell 恰好没设变量 | `backend/tests/conftest.py` 顶部两块（代码即权威）+ 复现测试 `test_langsmith_offline_guard.py` |
| 依赖面 | 三包同钉；新依赖需说明理由；抬版走独立 chore + 人工评估（含 Dependabot 自动抬版）；`langchain*` 保持零钉版；uvicorn 区间不动，starlette 显式钉 `>=1.3.1,<2`（覆盖 2026 同批 CVE，修复线 1.3.1，见 #198）；`qianfan` 的安装入口只多一个 build 期 setuptools，运行期依赖仍只以 `requirements.txt` 为源，mcp 的 `uvicorn>=0.31.1` 元数据用根目录 `overrides.txt` 覆盖（区间不变） | `requirements.txt` 注释 + `pyproject.toml` 头注 |
| 真实模型验证 | 发布前 / 换供应商时，按 [9 个场景](docs/manual-verification.md) **启服务、在网页端手动跑一遍**（CI 不再跑真模型）；README **不记**跑过的时间与模型 | `docs/manual-verification.md` + [ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)「三笔账」2 |
| 提交边界 | 副作用可逆或事先说明；提交只含本次意图内的源码与文档（`.env` / `data/` / `frontend/dist` 是本地态）；`push --force` 类默认拒绝 | `.gitignore` + 全局 `~/.agents/AGENTS.md` |
| 发布与合并惯例 | 功能 / 依赖 / 代码走 PR + CI 全绿；docs 小修可直推；依赖类 PR 先人工评估 | `docs/agents/issue-tracker.md`「Release conventions」 |

## 任务路由

| 任务触发 | 先读（权威源） | 完成判据 |
|---|---|---|
| 新票实施（代码） | `docs/agents/workflow.md` | 实施/评审分离：qoder 实施 + `code-review` 自评审 + `neat-freak` 文档收尾（步 3.5，能力面/接口/配置语义变了必须同步文档）+ 提 PR；opencode 独立评 PR；CI 全绿 + 评审通过后由评审侧/用户合并 |
| 后端改动 | `pyproject.toml` | 门禁命令原样跑且 0 错；零 ignore / override |
| 前端改动 | `frontend/package.json` + `.github/workflows/ci.yml` 的 frontend-build job；视觉 / 交互 / 组件**设计规则（目标态，未交付）**加读 [`docs/frontend-design.md`](docs/frontend-design.md) | `npm ci` 口径；`npm run typecheck` 与 `npm run build` 通过；**动到对应判据面再加该面的 `check:*`**：SSE 事件面（发布点 / 注册表 / §3.4 表）→ `npm run check:sse`；键盘与 #189 回归见设计基线 §6 / §10 |
| stop / resume / checkpoint / durability | `docs/incremental-arch-p3-resume.md` + `docs/migration-langgraph-1x.md`（§2 存储守卫、§5 durability） | TaskManager 权威信号（`_stop_flags`）在；两类 409 拒绝逐条成立（非 INTERRUPTED、无 checkpoint） |
| 计划 / 执行两模式 / 状态机 | `docs/adr/0008`（决策）+ `docs/archive/pivot-plan-build.md`（切法与片号）+ `backend/core/agent/graph.py`（拓扑）+ `backend/services/plan_file.py`（计划文件） | 一个任务 = 一段多轮对话；**一条 loop 一个 `agent` 节点**；**默认执行模式**、用户显式切才走计划（黏性）；一轮的最后一次 `agent` 调用没有工具调用即停下；状态三值、无「完成」终态；步数上限按**轮**计；计划 = 任务目录里的 `.qianfan/plans/<task_id>.md`（唯一来源） |
| 依赖升级 | `requirements.txt` 注释 | 本地复现 CI 确切命令后合并 |
| 开 issue / 定标签 / 领域文档 / 文档归属 | `docs/agents/` 三件套（issue-tracker / triage-labels / domain——「文档归属」表在 domain） | 标签流转按 triage-labels；文档一律落 `docs/` |
| 语义/设计歧义需拍板（如产物定义、面板去留） | 调研 opencode / codex / pi / dsh 等开源 agent 仓库的同类处理（GitHub 实码为证，不靠记忆）；读料可交后台代理跑 `research` skill | 调研产物落 `docs/`（按 domain「文档归属」表，带文件级引用），票面只留结论 + 链接，再拍板 |

## 验证入口与完成证据

```bash
# 离线回归（唯一权威）——解释器一律 .venv311；根目录 .venv 是 3.13 且无依赖，不要用
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q

# Windows 敏感面（与 CI backend-windows-smoke 同清单；改路径 / 进程 / os 分支时跑）
.\.venv311\Scripts\python.exe -m pytest backend/tests/test_static_guard.py backend/tests/test_static_frontend.py backend/tests/test_gitignore_visibility.py backend/tests/test_workspace_files_api.py backend/tests/test_workspace.py backend/tests/test_p2_mcp.py backend/tests/test_bash_tool.py -q -rs

# 质量门禁（与 CI backend-test 同命令；工具链：pip install -r requirements-dev.txt）
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy

# 文档链接（改 docs/ 或根级 md 必跑；只校验 tracked Markdown 的相对链接）
.\.venv311\Scripts\python.exe scripts\check_doc_links.py

# 真实模型验证（发布前 / 换模型）：启服务、在网页端手动跑一遍（CI 不再跑真模型）
.\.venv311\Scripts\python.exe -m uvicorn backend.main:app --port 8000 --reload
cd frontend; npm run dev

# 前端（cd frontend，Node 22）；CI 同口径 = npm ci → npm run typecheck → npm run check:sse → npm run build
npx tsc --noEmit     # 本地快检，0 错误
npm run check:sse    # SSE 事件词汇表四处对齐（#92）；改事件面必跑，细则见 docs/architecture.md §3.4
```

- 提交前钩子（[#114]）：`.venv311\Scripts\python.exe -m pre_commit install`（每台机器一次，含 commit-msg 钩子）；手动全量 `pre_commit run --all-files`；跳过 `SKIP=mypy` 单个或 `--no-verify` 全跳（跳了写清理由）。pre-commit entry 用仓库相对 `.venv311`，无本机绝对路径。钩子清单与实测耗时见 `.pre-commit-config.yaml` 头注。
- **Windows 冒烟清单**（`.github/workflows/ci.yml` 的 `backend-windows-smoke`）：新增「只在 Windows 跑」的行为测试（`os.name`、`sys.platform`、路径分隔符）时，把对应测试文件同时加进该 job 的 pytest 命令与上面那条本地命令，两边保持同一清单；**进程树 / 子进程类由 B1（#197）起逐个验证并纳入**（连跑 3 次稳定才入清单）；新增同类文件仍按「一个文件一个 PR + 连跑 3 次观察」纳入，抖先修环境适配、不放宽断言；当前清单以上面那条命令为准。
- 报告完成时区分四种状态：**通过 / 跳过 / 未运行 / 产物验收**；未执行的项写明原因与影响面——`exit 0` ≠ 全验收。
- 既有测试只增不减、不得跳过或放宽；文档不写现役测试计数（数字必腐，以实跑为准）。
- 改接口 / 配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。

## Agent skills

> 本节由 `setup-matt-pocock-skills` 技能生成（2026-09-15），供 mattpocock 工程技能组读取；
> 下面四个 `###` 标题是它们的读取入口。细节改 `docs/agents/*.md` 即可，不必重跑技能。
> 技能组来源 `mattpocock/skills`：本地克隆（路径因机器而异；HEAD `6fd9479`，
> 2026-10-07 同步）。上游有更新先 diff 克隆再改本仓文档，别照记忆改。

### Issue tracker

GitHub Issues（`renjianguojinqianfan/langgraph-agent`），走 `gh` CLI；PowerShell 多行 body 用 `--body-file`。See `docs/agents/issue-tracker.md`.

### Triage labels

五个规范角色默认标签，无改名映射（`ready-for-agent` / `wontfix` 已在用）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根 `GLOSSARY.md`（指针式入口，词汇权威在 `backend/core/agent/state.py`，节点名见 `graph.py`）+ `docs/adr/`（0001–0010；**状态总表见 `docs/agents/domain.md` 文首**）。See `docs/agents/domain.md`.

### Implementation workflow

新票实施分工：qoder 切分支 + `implement` + `code-review` 自评审 + `pr` 写 PR body + `neat-freak` 文档收尾 + 提 PR；合并后 `retro` 复盘；opencode 独立评 PR（不改代码只提返工）；评审侧/用户合并。多票大工程可整 spec 路由 `implement-spec`。See `docs/agents/workflow.md`.

