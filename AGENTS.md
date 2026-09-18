# AGENTS.md - langgraph-agent

> 本文件是项目级 agent 手册：**短入口 + 安全边界不变量 + 任务路由 + 验证入口**。
> 不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律；事实与细节按指针找权威源，本文件不存第二份。

## 项目定位与适用范围

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → planner→executor→tool→reflect 循环 → 工具调用 → SSE 实时可视化；深度在运行时（续跑 / 确认闸门 / 完成验证 / 熔断 / 工具生态 / 两层注入 / 回滚）。能力见 `README.md`「能力」，交付长史见 `OVERVIEW.md`，设计与 spec 见 `docs/`。

- **本文件面向外部开发 agent**（改这个仓库时读）。
- **产品运行时两层注入**只读 home 层 `~/.agents/AGENTS.md` → 工作区根 `AGENTS.md`（= 沙箱根）；盘根 / 中间层 / 子目录一律不读，`docs/` 不进注入。实现：`backend/core/agent/inject.py`。

## 跨任务安全边界（不变量）

| 触发词 | 不变量 | 权威源 |
|---|---|---|
| 密钥 | 只走环境变量注入；`.env` 的 `llm_api_key` 保持留空；不硬编码、不打印明文，只报 set / not set | `scripts/LIVE_E2E.md` §4 安全规范 + `.env.example` 注释 |
| 熔断层零改动（冻结区） | `resilience.py` / `registry.py` 零改动（CI guard 机械拦截）；`[OVERRIDE]` 是技术开口、不等于人工批准；MCP/Git/OpenAPI 工具由 `task_manager` 显式追加（不经 `@register`） | `docs/adr/0002`「边界与例外」 |
| `_needs_confirm` 重算块 | `nodes.py` `human_confirm_node` 中该块一字不动（P0 死循环根因）；新确认逻辑走 `_confirmed_ids` / `_rejected_ids` | **无机械守卫**（guard 不覆盖它），改动前问人；`docs/incremental-arch-p3-resume.md` §3 |
| 确认闸门 | 危险工具执行前经 `human_confirm`，批准 / 拒绝后重算待确认项；评测态例外：`--auto-approve` 仅 `backend/headless.py` 实例级，服务端永不传 | `README.md`「能力」危险操作闸门 + 「无人值守执行」评测态说明 |
| 质量门禁 | `ruff check backend scripts` 与 `mypy` 必须 0 错；类型错误用真实签名修复（不留 ignore / `noqa`）；mypy 零 override；工具链钉在 `requirements-dev.txt` | `pyproject.toml` 注释（规则集与每条取舍理由）——改配置前先读 |
| 测试隔离 | 离线测试不受本地 `.env` 影响；`conftest.py` 顶部覆盖块（`setdefault` 语义）保持原样 | `backend/tests/conftest.py` 顶部（代码即权威） |
| 依赖面 | 三包同钉；新依赖需说明理由；抬版走独立 chore + 人工评估（含 Dependabot 自动抬版）；`langchain*` 保持零钉版；uvicorn / starlette 区间不动 | `requirements.txt` 注释（三包理由 / mcp `--no-deps`） |
| 真实模型验证 | 发布前 / 换供应商必须跑 `live_e2e.py` 双场景 + `live_skill_test.py` 两腿；CI live job 有 key gate——绿 ≠ 真实模型跑过 | `scripts/LIVE_E2E.md`（唯一权威） |
| 提交边界 | 副作用可逆或事先说明；提交只含本次意图内的源码与文档（`.env` / `data/` / `frontend/dist` 是本地态）；`push --force` 类默认拒绝 | `.gitignore` + 全局 `~/.agents/AGENTS.md` |
| 发布与合并惯例 | 功能 / 依赖 / 代码走 PR + CI 全绿；docs 小修可直推；依赖类 PR 先人工评估 | `docs/agents/issue-tracker.md`「Release conventions」 |

## 任务路由

| 任务触发 | 先读（权威源） | 完成判据 |
|---|---|---|
| 后端改动 | `pyproject.toml` | 门禁命令原样跑且 0 错；零 ignore / override |
| 前端改动 | `frontend/package.json` + `ci.yml` 的 frontend-build job | `npm ci` 口径；`npm run typecheck` 与 `npm run build` 通过 |
| stop / resume / checkpoint / durability | `docs/incremental-arch-p3-resume.md` + `docs/migration-langgraph-1x.md`（§2 存储守卫、§5 durability） | TaskManager 权威信号（`_stop_flags`）在；三类 409 拒绝逐条仍成立 |
| 熔断层零改动（冻结区）/ 确认逻辑 | `ci.yml` 的 guard job | guard 全绿 |
| 依赖升级 | `requirements.txt` 注释 | 本地复现 CI 确切命令后合并 |
| live / 换模型 | `scripts/LIVE_E2E.md` + `ci.yml` 的 live-e2e job | 先核免费额度 |
| headless / 评测台 | `README.md`「无人值守执行（P0-C headless）」 | 四级退出码 0/1/2/3 语义不变；`degraded` 标记不丢 |
| 开 issue / 定标签 / 领域文档 / 文档归属 | `docs/agents/` 三件套（issue-tracker / triage-labels / domain——「文档归属」表在 domain） | 标签流转按 triage-labels；文档一律落 `docs/` |

## 验证入口与完成证据

```bash
# 离线回归（唯一权威）——解释器一律 .venv311；根目录 .venv 是 3.13 且无依赖，不要用
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q

# 质量门禁（与 CI backend-test 同命令；工具链：pip install -r requirements-dev.txt）
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy

# 离线冒烟（无 Key / 无网络，CI 同款）
.\.venv311\Scripts\python.exe scripts/live_e2e.py --check
.\.venv311\Scripts\python.exe -m backend.headless --check

# 真实模型（发布前 / 换模型；细则见 scripts/LIVE_E2E.md）
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_e2e.py
LLM_API_KEY="$DASHSCOPE_API_KEY" .\.venv311\Scripts\python.exe scripts/live_skill_test.py

# 前端（cd frontend，Node 22）；CI 同口径 = npm ci → npm run typecheck → npm run build
npx tsc --noEmit     # 本地快检，0 错误
```

- 报告完成时区分四种状态：**通过 / 跳过 / 未运行 / 产物验收**；未执行的项写明原因与影响面——`exit 0` ≠ 全验收。
- 既有测试只增不减、不得跳过或放宽；文档不写现役测试计数（数字必腐，以实跑为准）。
- 改接口 / 配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。

## Agent skills

> 本节由 `setup-matt-pocock-skills` 技能生成（2026-09-15），供 mattpocock 工程技能组读取；
> 下面三个 `###` 标题是它们的读取入口。细节改 `docs/agents/*.md` 即可，不必重跑技能。

### Issue tracker

GitHub Issues（`renjianguojinqianfan/langgraph-agent`），走 `gh` CLI；PowerShell 多行 body 用 `--body-file`。See `docs/agents/issue-tracker.md`.

### Triage labels

五个规范角色默认标签，无改名映射（`ready-for-agent` / `wontfix` 已在用）。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根 `CONTEXT.md`（未建）+ `docs/adr/`（0001 / 0002）。词汇表在 `backend/core/agent/state.py`（节点名见 `graph.py`）。See `docs/agents/domain.md`.

