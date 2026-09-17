# AGENTS.md - langgraph-agent

> 本文件是 `langgraph-agent` 的项目级 agent 手册：**短入口 + 安全边界 + 任务路由 + 验证入口**。
> 不重复全局 `~/.agents/AGENTS.md` 的工作循环纪律，只写本仓库特有的东西；事实类信息按「任务路由」找权威源，本文件不存第二份。

## 项目定位与适用范围

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台，前后端一体：自然语言下发任务 → planner→executor→tool→reflect 循环 → 工具调用 → SSE 实时可视化。深度押在运行时：断点续跑、危险操作确认闸门、完成验证、熔断重试、MCP/Git/OpenAPI 工具链、两层上下文注入 + skills 运行时、工具结果逐出、工作区回滚。能力现状见 `README.md`「能力」，历次交付长史见 `OVERVIEW.md`，各轮设计与 spec 见 `docs/`。

- **本文件面向外部开发 agent**（改这个仓库时读）。
- **产品运行时的两层注入**只读：home 层 `~/.agents/AGENTS.md` → 工作区根 `AGENTS.md`（= 沙箱根）；盘根与中间层一律不读、子目录不递归，开发文档（`docs/`）不进注入。实现：`backend/core/agent/inject.py`。

## 跨任务安全边界

- **密钥**：只走环境变量注入（`.env` 的 `llm_api_key` 保持留空）；绝不硬编码、绝不打印明文，只报 set / not set。
- **熔断层零改动（冻结区）**：`backend/core/tools/resilience.py`（CircuitBreaker / with_retry / ToolExecutor）与 `registry.py` 零改动，由 CI `guard-protected-files` 机械拦截——`[OVERRIDE]` 只是技术开口、不等于人工批准，真需要改先停下问人。MCP/Git/OpenAPI 工具由 `task_manager.py` 显式追加（`_load_mcp_tools` / `_load_git_tools`），不经 `@register`。
- **`_needs_confirm` 重算块（冻结区，无机械守卫）**：`backend/core/agent/nodes.py` 的 `human_confirm_node` 中该块是 P0 死循环根因，**一字不动**；新增确认逻辑走 `_confirmed_ids` / `_rejected_ids` 流程。（P3 在 else 分支追加的 stop-forced 标记属 Issue #4 行为，保留。）
- **确认闸门**：危险工具（code_exec / git_commit / http 写类 / MCP 写类）执行前经 `human_confirm` 闸门，批准/拒绝写回后**重算**是否仍有待确认项。**评测态例外**：`--auto-approve` 仅 `backend/headless.py` 实例级设置（`TaskManager(auto_approve=True)` → `confirm_enabled=False`），FastAPI 服务端路径永不传——不是全局关闸开关。
- **质量门禁不得放宽**：`ruff check backend scripts` 与 `mypy` 基线全净；类型错误用真实签名修复（`# type: ignore` / `# noqa` 是债，`warn_unused_ignores = true` 还会把陈旧 ignore 变成新错）；mypy 保持零 override；工具链版本钉在 `requirements-dev.txt`；规则集与每条「刻意不启用」的理由在 `pyproject.toml`——**改配置前先读那段**。
- **测试隔离**：离线测试必须不受本地 `.env` 影响——`backend/tests/conftest.py` 顶部用环境变量覆盖（`setdefault` 语义）；**改 conftest 勿破坏这段隔离**，否则本地 live 配置会污染全部离线用例。
- **依赖面**：`langgraph>=1.2,<1.3` + `langgraph-checkpoint==4.2.0` + `langgraph-checkpoint-sqlite==3.1.1` 三包同钉（合起来是 resume 契约的存储面，理由在 `requirements.txt` 注释）；抬版走独立 chore + 人工评估，Dependabot 自动抬版勿直接合；`langchain*` 不得重新钉版（0 import 且与 1.x 冲突）；uvicorn/starlette 区间不动（mcp 用 `--no-deps` 装是既定代价）。
- **真实模型验证**：离线测试只证明确定性；发布前 / 换供应商必须跑真实 LLM（`scripts/live_e2e.py` 双场景 + `scripts/live_skill_test.py` 两腿）。CI 的 live job 有 **key gate**：secrets 为空时整步跳过、job 仍绿——**绿 ≠ 真实模型跑过**，看结论前先看 gate 输出。完整步骤、额度核验与 gate 语义见 `scripts/LIVE_E2E.md`（唯一权威）。
- **提交边界**：新副作用（写库 / 建文件 / 起服务）可逆或事先说明；提交不得带上 `.env` / `data/` / `frontend/dist` 的密钥或生成物。
- **发布与合并惯例**（分级 PR/直推、dependabot、合并方式）：见 `docs/agents/issue-tracker.md`「Release conventions」。

## 任务路由

| 任务触发 | 先读（权威源） | 必须满足 |
|---|---|---|
| 后端改动 | 本文件「跨任务安全边界」+ `pyproject.toml` | 门禁命令原样跑（见「验证入口」）；零 ignore / override |
| 前端改动 | `frontend/package.json` + `ci.yml` 的 frontend-build job | `npm ci` 口径；`npx tsc --noEmit` 0 错 |
| stop / resume / checkpoint / durability | `docs/incremental-arch-p3-resume.md` + `docs/migration-langgraph-1x.md`（§2 存储守卫、§5 durability） | TaskManager 权威停止信号（`_stop_flags` / `is_stop_flagged()`）；三类 409 拒绝不可放松 |
| 冻结区 / 确认逻辑 | 本文件「熔断层零改动」「`_needs_confirm` 重算块」条 + `ci.yml` 的 guard job | guard 只覆盖 resilience / registry；`_needs_confirm` 无机械守卫、改动前问人 |
| 依赖升级 | 本文件「依赖面」+ `requirements.txt` 注释 | 独立 chore；先本地复现 CI 确切命令再合并 |
| live / 换模型 | `scripts/LIVE_E2E.md` + `ci.yml` 的 live-e2e job | 先核免费额度；key gate 跳过 ≠ 通过 |
| headless / 评测台 | `README.md`「无人值守执行（P0-C headless）」 | 四级退出码 0/1/2/3；`COMPLETED` 可能含 `degraded` 标记 |
| 发单 / 标签 / 领域 / 文档归属 | `docs/agents/` 三件套（issue-tracker / triage-labels / domain） | 标签流转按 triage-labels；文档一律落 `docs/`，归属表见本文件「文档归属」 |

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

- 报告完成时区分四种状态：**通过 / 跳过 / 未运行 / 产物验收**；未执行的项写明原因与影响面——`exit 0` ≠ 全验收（例：`--check` 只验接线，不代表真实模型跑过）。
- **既有测试只增不减、不得跳过或放宽**。
- 改接口 / 配置后同步 `.env.example` 与 `README.md`（含新配置前缀）。

## Agent skills

> 本节由 `setup-matt-pocock-skills` 技能生成（2026-09-15），供 mattpocock 工程技能组
> （`triage` / `to-spec` / `to-tickets` / `wayfinder` / `domain-modeling` / `implement` …）读取。
> 细节改 `docs/agents/*.md` 即可，不必重跑技能——只有换 issue tracker 或推倒重来才需要重跑。

### Issue tracker

GitHub Issues（`renjianguojinqianfan/langgraph-agent`），一律走 `gh` CLI；Windows PowerShell 下多行 body 用 `--body-file`，不用 heredoc。See `docs/agents/issue-tracker.md`.

### Triage labels

沿用五个规范角色的默认标签字符串（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`），无改名映射；其中 `ready-for-agent` 与 `wontfix` 仓库里早已在用。See `docs/agents/triage-labels.md`.

### Domain docs

Single-context：根目录一份 `CONTEXT.md`（仍未建，由 `/domain-modeling` 惰性创建）+ 一份 `docs/adr/`（已有 0001 / 0002）。在其就位前，事实词汇表是 `backend/core/agent/state.py`（节点名见 `graph.py`），冻结决策等价物是本文件「跨任务安全边界」。See `docs/agents/domain.md`.

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