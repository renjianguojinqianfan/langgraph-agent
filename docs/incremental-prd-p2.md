# 增量 PRD（P2 完整能力）— 基于 LangGraph 的自主任务 Agent

> 文档性质：**增量 PRD（仅描述本次变更部分，不重写整份 PRD）**
> 产品经理：许清楚（Xu）　|　版本：v0.1（P2 增量草案）　|　日期：2026-08-24
> 关联文档：现有 PRD `docs/prd.md`、架构 `docs/architecture.md`、P0 增量 `docs/incremental-prd-p0.md` / `docs/incremental-arch-p0.md`、P1 增量 `docs/incremental-prd-p1.md` / `docs/incremental-arch-p1.md`
> 项目根目录：`E:\code\demo\langgraph-agent\`

---

## 0. 背景与对标依据

本次为 **P2 完整增量实施**：在 P0（上下文压缩、熔断+重试、插件注册、trace 落盘）与 P1（风险扫描、子 Agent、RAG 记忆、辅助模型、基础鉴权、OpenAPI 工具）已交付的基础上，补齐市面对标中剩余 P2 能力，共 **2 项**，走轻量 SOP。

| 市面能力 | 本次对标项 | 说明 |
|----------|-----------|------|
| Claude Code 原生 MCP（6000+ 外部工具） | 项1 **MCP 客户端接入** | Agent 可通过 MCP 接入外部工具服务器，把 MCP 服务器暴露的工具动态注册为 BaseTool 供 LLM 调用，补齐"无 MCP 生态接入"差距 |
| Claude Code / Codex CLI Git 全流程（branch/commit/PR） | 项2 **Git 工具** | Agent 可在代码仓库内执行 Git 操作（status/diff/commit/log/branch/checkout/init），补齐"无 Git 工具"差距 |

> 说明：对标项 PR（Pull Request）与远程操作（push/pull 等）**本次不做**（见 §6 范围边界），仅做本地 Git 工作流闭环，避免引入远端凭据管理与复杂冲突处理。

---

## 1. 增量目标

### 一句话定位
在**不破坏现有编排内核（StateGraph + SSE + P0/P1 全部能力）**的前提下，补齐"外部 MCP 工具生态接入"与"仓库内 Git 操作"两项能力，使 Agent 达到可接入市面主流 MCP 服务器、可对代码仓库执行受控 Git 工作流的主流 Agent 水准（对标 Claude Code / Codex CLI 的核心差距）。

### 两项各自目标与成功标准（可量化验收）

| 项 | 目标 | 成功标准（可量化） |
|----|------|--------------------|
| **1 MCP 客户端接入** | Agent 通过 MCP（stdio 传输为主）连接外部工具服务器：启动时 initialize + list_tools，将服务器暴露的工具动态包装为 BaseTool 注册进工具集；LLM 选中后 call_tool 转发并映射 ToolResult；连接/调用失败不崩溃主任务；生命周期可优雅关闭 | 配置 1 个本地 stdio MCP server（如 `npx` 启动的官方 filesystem 或测试 echo server）→ 启动后 `list_tools()` 新增该 server 暴露的全部工具（≥1 个），name/args_schema 与服务器 `inputSchema` 一致；LLM 端到端调用 1 次 MCP 工具成功并解析结果；杀掉 server 进程后再次调用返回 `success=False` + 明确 error 且主任务继续（零崩溃）；连续失败达阈值触发 `tool_circuit_open`（复用 P0 熔断）；`mcp_servers=[]`/未启用时现有功能零回归（无 mcp 工具、无额外进程） |
| **2 Git 工具** | Agent 可在配置的代码仓库内执行受控 Git 操作：只读类（status/diff/log/branch）直接执行，改写类（commit/checkout/init）强制人工确认，危险命令（如 `push --force`/`reset --hard`/`clean -fdx` 等）默认拒绝；非 git 目录明确报错 | 在 git 仓库内 7 个工具全部可用：`git_status` 返回变更清单、`git_diff` 返回差异文本、`git_log` 返回最近提交、`git_branch` 返回分支列表、`git_commit` 提交成功返回 commit hash、`git_checkout` 切换成功返回新分支、`git_init` 初始化成功；非 git 目录调用任一工具返回明确错误（not a git repository）且不崩溃；`git_commit`/`git_checkout`/`git_init` 触发 `human_confirm_required` 且未确认不执行；黑名单命令（`push --force`/`reset --hard`/`clean -fdx`/`rebase`/`merge` 等）被拒绝并给出明确错误；含特殊字符的 commit message 不产生命令注入（参数化 subprocess、无 shell） |

---

## 2. 用户故事（按角色/场景）

| # | 角色 | 用户故事 | 价值 |
|---|------|----------|------|
| US-P2-1 | 开发者 | 作为开发者，我希望在 `.env` 里配置一个 MCP 服务器（如 filesystem、数据库、GitHub 官方 server），Agent 启动后自动接入并把这些外部工具当作内置工具一样让 LLM 调用，从而复用 MCP 生态里现成的 6000+ 工具。 | 生态接入、可扩展 |
| US-P2-2 | 开发者 | 作为开发者，我希望 Agent 能在我的代码仓库里查看状态/差异、提交、切分支，从而在"改代码 → 提交"的自主任务中完成版本管理闭环，而不必切到终端手动执行。 | 任务闭环、提效 |
| US-P2-3 | 安全负责人/用户 | 作为用户，我希望 Agent 执行 `git commit`/`git checkout`、或调用 MCP 写类工具（如 delete/update/send）前先要我确认；`push --force`/`reset --hard` 这类危险命令被直接拒绝，避免误操作毁掉代码或数据。 | 安全、可控 |
| US-P2-4 | 运维/部署者 | 作为运维，我希望某个 MCP 服务器挂了或超时只影响该工具调用（返回失败、熔断），不拖垮整个任务进程；重启应用后无残留子进程。 | 稳定、可运维 |

---

## 3. 需求池（本次全部 P0 内子点）

> 优先级说明：本次增量内 2 项均为 **P0（必须交付）**；每项拆子点，功能点 + 可量化验收 + 改动点。

### 项1：MCP 客户端接入

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 1.1 | mcp_servers 配置 | `Settings` 新增 `mcp_enabled`（总开关）与 `mcp_servers`（JSON 字符串 → List）；每项字段：`name`、`command`、`args[]`、`env{}`、`enabled`、`cwd`（可选）；`url`/`transport` 字段为 SSE/HTTP 预留（本 P2 不实现）；另加 `mcp_timeout_sec`、`mcp_force_confirm`（确认覆盖清单，JSON 数组） | 配置 2 个 server（1 enabled / 1 disabled）→ 仅连接 enabled 的那个；`mcp_servers=[]` 或 `mcp_enabled=false` → 零 mcp 工具、零子进程、现有功能零回归；非法 JSON → warning 且启动继续（不崩溃） | `backend/config.py` + `.env.example` |
| 1.2 | 生命周期：启动连接 | 新增 `McpClientManager`：启动时（`TaskManager.__init__` 内）对每个 enabled server 建立 stdio 会话（`subprocess` 子进程，如 `npx` 启动），执行 MCP `initialize` + `list_tools`；记录每 server 的连接状态与工具清单 | 对 `npx @modelcontextprotocol/server-filesystem <dir>` 或测试 echo server：启动后连接成功且列出 ≥1 个工具；连接失败（命令不存在/启动报错）→ 仅 warning、该 server 标记 error、启动继续 | 新增 `backend/core/mcp/__init__.py`、`backend/core/mcp/client.py`（`McpClientManager`）；改 `backend/services/task_manager.py`（初始化 + `_load_mcp_tools()`） |
| 1.3 | 工具注册 | 每个 MCP 工具包装为 `MCPTool(BaseTool)`：name 采用 `mcp__{server}__{tool}`（Claude Code 同款命名，天然避免跨 server 冲突）；description 用服务器工具描述；`args_schema` 映射 MCP `inputSchema`；注册进 `_tools`/`_tool_schemas`（与 OpenAPI 工具同一追加路径，冲突保留先注册者 + warning） | server 暴露 N 个工具 → `list_tools()`/LLM 可用工具新增 N 个，name 唯一、args_schema 与 `inputSchema` 一致（抽 1 个含必填参数的工具校验）；与内置工具同名冲突 → 保留内置 + warning | 新增 `backend/core/tools/mcp_tool.py`（`MCPTool`）；改 `backend/services/task_manager.py`、`backend/core/tools/registry.py`（如需要） |
| 1.4 | 工具调用转发 | `MCPTool.run(**kwargs)` → 经所属 server 的 session `call_tool(name, arguments)` → 将返回 content 块（text / structuredContent）映射为 `ToolResult`（`data` 结构化、`success` 按协议 isError）；异步 SDK 调用做同步包装（`asyncio.run`/事件循环桥接） | LLM 端到端调用 1 次 MCP 工具成功并解析出结果（如 filesystem `read_text` 返回文件内容）；返回的 `data` 含 `server`/`tool` 来源字段便于前端展示 | `backend/core/tools/mcp_tool.py` |
| 1.5 | 失败处理与熔断重试 | server 不可用/超时/协议错误 → `ToolResult(success=False, error=...)` 且**不抛未捕获异常**；`MCPTool` 保留 BaseTool 默认 `retryable=True`/`circuit_breaker=True`，直接复用 P0 `ToolExecutor`（零改动） | 杀掉 server 进程后调用 → `success=False` + 明确 error，主任务继续（后续步骤正常）；连续失败达 `tool_failure_threshold` → `tool_circuit_open` 事件（前端可展示）；超时受 `mcp_timeout_sec` 约束 | `backend/core/tools/mcp_tool.py`（无 resilience 改动，复用 `ToolExecutor`） |
| 1.6 | 安全与确认 | 危险判定：`MCPTool` 新增 `_needs_confirm(args)`——按工具方法名/描述启发式（写类动词 write/create/delete/update/edit/insert/remove/send/push/upload/execute 等 → True）叠加 `mcp_force_confirm` 覆盖清单；`executor` 对 `MCPTool` 增加 per-call 判定（仿 `http_request` WRITE_METHODS 特判），命中 → `need_confirm=True` 走现有 `human_confirm` 流程 | 写类 MCP 工具调用 → `tool_call` 事件 `need_confirm=True`，确认前**不执行**（可证：未确认无 `tool_result`）；拒绝 → `status=skipped`；读类工具直接执行；`mcp_force_confirm` 中列出的工具即使读类也需确认 | 改 `backend/core/agent/nodes.py`（executor 增加 MCP 判定分支）；`backend/core/tools/mcp_tool.py` |
| 1.7 | 优雅关闭 | `McpClientManager.cleanup()` 关闭全部 session 并终止 stdio 子进程；`TaskManager.shutdown()` 调用；`main.py` lifespan 退出阶段调用 | 应用退出/重建后无残留 MCP 子进程（进程列表可证）；重复构造 `TaskManager` 不泄漏连接 | 新增 `backend/core/mcp/client.py`；改 `backend/services/task_manager.py`、`backend/main.py`（lifespan 收尾） |
| 1.8 | 可观测 | 新增 `GET /api/mcp/servers`：返回每 server 的 `{name, transport, status(connected|error|disabled), tools_count, error?}`；SSE 事件 `mcp_connected`/`mcp_failed`（可选，接入诊断） | 接口可查 MCP 服务器状态与工具数；未启用时返回空列表（零回归） | 改 `backend/api/routes.py`、`backend/api/schemas.py`（`McpServerInfo`）、`frontend/src/types/index.ts`（类型对齐，可选面板） |

**配置项（config.py / .env.example 新增）**：`mcp_enabled`（默认 true）、`mcp_servers`（默认 `[]`）、`mcp_timeout_sec`（默认 30）、`mcp_connect_timeout_sec`（默认 15）、`mcp_force_confirm`（默认 `[]`）。

---

### 项2：Git 工具

| ID | 子点 | 功能点 | 验收标准（可量化） | 改动点 |
|----|------|--------|--------------------|--------|
| 2.1 | git_status | `GitStatusTool`（name=`git_status`）：运行 `git status --short`（或 `--porcelain=v1`）+ 当前分支；入参 `{path?}`（仓库子路径，默认仓库根） | git 仓库内返回变更文件列表（新增/修改/删除/未跟踪）+ 当前分支；非 git 目录返回 `success=False` + 明确 error（not a git repository）；无变更返回空列表 | 新增 `backend/core/tools/git_tools.py`：`GitTool` 基类（参数化 `subprocess` runner，无 shell）+ 各子工具；`registry.py` 注册；`config.py` 新增 `git_enabled`/`git_repo_dir`/`git_timeout_sec` |
| 2.2 | git_diff | `GitDiffTool`（name=`git_diff`）：`git diff`（未暂存）+ `git diff --cached`（已暂存）；入参 `{staged?: bool, path?}` | 有改动时返回 diff 文本；无改动返回空；非 git 目录明确报错；`staged=true` 只返回已暂存差异 | 同 2.1 |
| 2.3 | git_commit | `GitCommitTool`（name=`git_commit`）：`git commit -m "<message>"`；**`requires_confirm=True`**；入参 `{message, path?}` | 提交成功返回 commit hash（`git rev-parse HEAD` 可证）；无任何变更提交 → `success=False` + 明确 error；空 message 被拒绝；执行前触发 `human_confirm_required`，未确认不执行 | 同 2.1 |
| 2.4 | git_log | `GitLogTool`（name=`git_log`）：`git log --oneline -n <limit>`（默认 10）；入参 `{limit?, path?}` | 返回最近 N 条提交（hash + message，含当前 HEAD）；空仓库返回空/提示（非报错）；非 git 目录明确报错 | 同 2.1 |
| 2.5 | git_branch | `GitBranchTool`（name=`git_branch`）：`git branch -a` + 当前分支标记；入参 `{path?}` | 返回分支列表与当前分支（`*` 标记或 `current` 字段）；非 git 目录明确报错 | 同 2.1 |
| 2.6 | git_checkout | `GitCheckoutTool`（name=`git_checkout`）：`git checkout <branch>`；**`requires_confirm=True`**；入参 `{branch, path?}` | 切换成功返回新分支名；分支不存在 → `success=False` + 明确 error；执行前触发确认，未确认不执行 | 同 2.1 |
| 2.7 | git_init | `GitInitTool`（name=`git_init`，视情况交付）：`git init` 在指定目录初始化；**`requires_confirm=True`**；入参 `{path?}` | 非 git 目录初始化成功（返回 `.git` 已创建）；已是 git 仓库返回提示（非报错）；执行前触发确认 | 同 2.1 |
| 2.8 | 仓库约束与安全 | 所有 Git 工具仅在 `git_repo_dir`（默认 `data/repos`）内工作，路径越界拒绝；**参数化 subprocess（`["git", "-C", repo, ...]`，`shell=False`）**，杜绝命令注入；危险命令黑名单：`push`/`pull`/`fetch`/`clone`/`remote`（远程类）、`reset --hard`、`clean -fdx`、`rebase`、`merge`、`cherry-pick`、`revert`、`rm`、`branch -D`、`tag -d`、`--force`/`-f` 改写 → **默认拒绝（blocked，返回明确错误）**或按确认策略放行（见 Q4 默认：黑名单一律拒绝） | 黑名单命令被拒绝且不执行（无副作用）；`message` 含 `;`/`&&`/`$(...)` 等特殊字符不产生注入（执行结果仅含字面 message）；路径 `../` 越界被拒；单次调用超时（`git_timeout_sec`）被强制中断 | `backend/core/tools/git_tools.py`；`backend/utils/sandbox.py`（如需要新增参数化命令 runner） |
| 2.9 | 与 code_exec 沙箱协同 | 独立实现（架构师定）：Git 工具**不经 `code_exec` 任意代码执行**，用专用参数化 runner 直接调 `git` 二进制；与沙箱正交：`git_repo_dir` 默认独立于 artifacts 沙箱，路径白名单校验 | Git 工具可在代码仓库内独立运行，不受 code_exec 沙箱临时目录限制；与 `code_exec` 互不干扰（零回归） | `backend/core/tools/git_tools.py`；`backend/services/task_manager.py`（注入 `git_repo_dir`） |

**配置项（config.py / .env.example 新增）**：`git_enabled`（默认 true）、`git_repo_dir`（默认 `data/repos`）、`git_timeout_sec`（默认 30）。

---

## 4. UI / 可视化影响

> 前端本次以**必要联动 + 极轻量新面板**为主，尽量复用现有组件（StepTimeline / StepDetail / ConfirmDialog / TraceTab / TaskPanel）。

| 项 | 是否需要前端 | 草图要点 |
|----|--------------|----------|
| **项1 MCP** | ⚠️ 可选（轻量） | 设置/诊断面板新增 **MCP 服务器卡片区**：每个 server 一行 `{名称 | stdio | ● 已连接/○ 错误 | 工具数 N | 错误信息}`，数据来自 `GET /api/mcp/servers`；工具调用展示**完全复用**现有 StepDetail（`mcp__filesystem__read_text` 等按普通工具渲染）；收到 `mcp_connected/mcp_failed` 事件可在该面板打状态角标（可选）。非强需求。 |
| **项2 Git** | ✅ 需要（联动） | 无新页面；`git_commit`/`git_checkout`/`git_init` 调用时复用现有 `ConfirmDialog`（标题"Git 操作确认"，展示命令与参数）；`StepDetail` 对 `git_diff`/`git_log` 结果用 `<pre>` 等宽展示 diff/日志文本；时间线 `tool_call` 节点按普通工具渲染即可。 |
| 前端类型 | ✅ 必要 | `frontend/src/types/index.ts` 追加 `McpServerInfo` 类型与可选 SSE 事件类型（`mcp_connected`/`mcp_failed`），与后端 `schemas.py` 对齐；如不做 MCP 面板则仅同步类型、不新增组件。 |

### 前端草图要点（ASCII，均复用现有布局）

```
┌───────────────┬──────────────────────────────────────┬──────────────┐
│ 历史任务       │  任务：检查仓库并提交改动             │ Step 详情     │
│ • 任务A   ✓   │  [消息流] [步骤] [子任务] [📜 Trace]  │              │
│               │  ⚙ git_status  → 3 files changed     │ 工具: git_diff│
│               │  ⚙ git_diff    → +12/-3 (README.md)  │ 入参/出参     │
│               │  ⚠ git_commit  [确认弹窗]             │  status: ✓   │
│               │     "feat: add mcp client" ✓ abc1234  │              │
│ 设置/诊断 Tab │  🧩 MCP 服务器:                       │              │
│               │  ● filesystem  stdio  5 tools         │              │
│               │  ○ my-db       error: connect fail    │              │
└───────────────┴──────────────────────────────────────┴──────────────┘
```

---

## 5. 待确认问题（极少量，均给默认值，不阻塞开发）

| # | 问题 | 建议默认值 | 影响 |
|---|------|-----------|------|
| Q1 | MCP 传输范围？ | **stdio 必做（P0）**；Streamable HTTP 仅预留 `url`/`transport` 字段与接口骨架（本 P2 不实现）；legacy SSE 不做（规范已弃用）。 | 影响项1 传输层实现范围与依赖 |
| Q2 | mcp_servers 配置格式？ | `Settings.mcp_servers` 为 **JSON 数组字符串**，每项 `{name, command, args[], env{}, enabled=true, cwd?, transport="stdio", url?}`；示例 `[{"name":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","./data"],"enabled":true}]` | 影响 1.1 配置解析与 `.env.example` |
| Q3 | Git 工具集与危险命令清单？ | 工具 7 个：`git_status`/`git_diff`/`git_commit`/`git_log`/`git_branch`/`git_checkout`/`git_init`；危险命令黑名单（默认拒绝）：远程类 `push/pull/fetch/clone/remote`、改写类 `reset --hard`、`clean -fdx`、`rebase`、`merge`、`cherry-pick`、`revert`、`rm`、`branch -D`、`tag -d`、任何 `--force/-f` 改写 | 影响 2.8 安全策略与验收 |
| Q4 | 确认策略（MCP 与 Git）？ | **MCP**：方法名/描述写类启发式 + `mcp_force_confirm` 覆盖清单（默认空）；**Git**：`git_commit`/`git_checkout`/`git_init` 静态 `requires_confirm=True`，黑名单命令一律拒绝（不放行） | 影响 1.6/2.3/2.6/2.7/2.8 行为 |
| Q5 | MCP SDK 依赖？ | 官方 Python SDK：`mcp>=1.2,<2.0`（当前 1.x 稳定线，Python ≥3.10；含 stdio client）。若引入受阻，退路为纯标准库（`subprocess` + JSON-RPC 自实现），架构师定 | 影响 requirements.txt 与实现成本 |

> 以上 5 项均不阻塞开发，文档已给默认值；确认后写入 `.env.example` 即可。

---

## 6. 范围边界（In / Out）

### 6.1 范围内（In Scope，本次 P2 增量）
- **项1** MCP **客户端**接入：stdio 传输（本地子进程）连接、`initialize`+`list_tools`、工具动态注册为 `BaseTool`、`call_tool` 调用转发、失败/超时不崩溃、复用 P0 熔断重试、写类工具确认、优雅关闭、`GET /api/mcp/servers` 可观测（轻量）。
- **项2** Git 工具集：`git_status`/`git_diff`/`git_commit`（确认）/`git_log`/`git_branch`/`git_checkout`（确认）/`git_init`（确认）；仓库根约束 + 参数化 subprocess（无 shell）+ 危险命令黑名单默认拒绝；与 code_exec 沙箱正交。

### 6.2 范围外（Out of Scope，本次不做）
- **MCP SSE / Streamable HTTP 传输实现**——仅预留配置字段与接口骨架，不实现远端连接（Q1）。
- **MCP server 端实现**（只做 client；不做 FastMCP 服务端、不托管 MCP server）。
- **MCP resources / prompts 原语接入**——本次只接入 tools 原语。
- **MCP 工具的动态热加载/热更新**（运行中增删 server）——仅启动时加载，改配置需重启。
- **Git 远程与协作操作**：`push`/`pull`/`fetch`/`clone`/`remote`、PR/merge request 流程、`rebase`/`merge`/`cherry-pick` 等（含冲突解决）——不在本次 7 工具内，且进入黑名单。
- **Git 凭据管理**（token/ssh key 注入、credential helper 集成）——不做。
- **Git 工作区 UI 面板**（文件级 diff 浏览、冲突编辑器）——本次仅 StepDetail 文本展示。
- **前端大改版**——本次仅复用现有组件 + 可选 MCP 诊断卡片，不新增页面骨架。

---

## 附：增量对现有模块的改动汇总

| 现有模块 | 本增量改动 |
|----------|-----------|
| `backend/config.py` + `.env.example` | 新增 `mcp_enabled`/`mcp_servers`/`mcp_timeout_sec`/`mcp_connect_timeout_sec`/`mcp_force_confirm`、`git_enabled`/`git_repo_dir`/`git_timeout_sec` |
| `backend/core/mcp/__init__.py`、`backend/core/mcp/client.py` | **新增**：`McpClientManager`（stdio 连接、initialize/list_tools、session 管理、cleanup） |
| `backend/core/tools/mcp_tool.py` | **新增**：`MCPTool(BaseTool)`（name=`mcp__{server}__{tool}`、args_schema 映射、`run`→`call_tool` 转发、`_needs_confirm(args)` 危险判定） |
| `backend/core/tools/git_tools.py` | **新增**：`GitTool` 基类（参数化 subprocess runner、仓库根校验、黑名单）+ `git_status`/`git_diff`/`git_commit`/`git_log`/`git_branch`/`git_checkout`/`git_init` |
| `backend/core/tools/registry.py` | Git 工具 `@register`；MCP 工具经 `TaskManager._load_mcp_tools()` 追加（冲突保留先注册者 + warning，与 OpenAPI 同路径） |
| `backend/core/agent/nodes.py` | `executor` 增加 `MCPTool` per-call 确认判定分支（仿 `http_request` WRITE_METHODS 特判） |
| `backend/services/task_manager.py` | 初始化 `McpClientManager` + `_load_mcp_tools()` 追加工具；`shutdown()` 调 MCP cleanup；Git 工具注入 `git_repo_dir` |
| `backend/main.py` | lifespan 退出阶段调用 `task_manager.shutdown()`（MCP 子进程收尾） |
| `backend/api/routes.py` | 新增 `GET /api/mcp/servers` |
| `backend/api/schemas.py` | 新增 `McpServerInfo` |
| `backend/api/sse.py` 协议（`frontend/src/types/index.ts`） | 可选新增事件类型 `mcp_connected`/`mcp_failed`（前端类型同步） |
| `frontend/src/**` | 仅类型对齐 + 可选 MCP 诊断卡片；Git 确认复用 `ConfirmDialog`、diff/log 复用 `StepDetail`（无新页面） |
| `requirements.txt` | 新增 `mcp>=1.2,<2.0`（若走官方 SDK；否则标准库零新增） |
