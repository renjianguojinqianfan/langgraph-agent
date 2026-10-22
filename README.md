# LangGraph 自主任务 Agent

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → **一条 loop、计划 / 执行两模式** → 工具调用 → 停下时人看工作区里的文件；FastAPI + React 前后端一体，SSE 实时可视化 + Trace 回放。

重点是运行时：多轮续说、checkpoint、模式切换、危险动作拦截、工具生态与可观测性，而不是一个 demo 界面。

**离线测试全绿**（零网络、零 Key、MockLLM）· CI 两个 job · 发布前按 [9 个真模型场景](docs/manual-verification.md) 在网页端跑一遍。

![qianfan 任务界面：计划 / 执行、模型下拉、子任务与工作区文件](docs/assets/qianfan-ui.png)

## 快速开始

日常使用（需要 [uv](https://docs.astral.sh/uv/) 与 Node 22；仓库外任意目录都能敲
`qianfan`，后端与网页同源起在 `127.0.0.1`）：

```bash
cp .env.example .env                             # LLM_API_KEY 走环境变量；或 use_mock_llm=true 离线
uv tool install -e . --overrides overrides.txt   # 每台机器一次
cd frontend && npm ci && npm run build && cd ..  # 前端产物；改前端后重跑
qianfan                                          # 自动开浏览器；--port / --no-browser 可选
```

`overrides.txt` 只做一件事：mcp 声明 `uvicorn>=0.31.1`，而本仓钉 `uvicorn<0.31`；
override 让干净环境装出与 `requirements.txt` 一致的版本。缺 `frontend/dist` 时
`qianfan` 会提示先打包；产物比 `frontend/src` 旧时会提示重跑 `npm run build`。

改前端时用开发启动（Vite 热重载，后端 `--reload`）：

```bash
python -m venv .venv311
.venv311\Scripts\activate                        # Git Bash: source .venv311/Scripts/activate
pip install -r requirements.txt
python start.py            # 虚拟环境请叫 .venv311；start.py 会拦住根目录的 .venv
```

## 核心模型

一个任务 = 一段多轮对话：

```text
agent ──有 tool call──> tool ──> agent
  └── 本轮最后一次调用没有 tool call ──> finish（停下，INTERRUPTED）
```

- **一条 loop、一个 `agent` 节点**；模式只换 prompt 和工具面：`plan` = 只读 `explore` 档，`build` = 全部已挂载工具。默认 `build`，开关黏性。
- **停下 = 这一轮的最后一次 `agent` 调用没有工具调用**；状态只有 `RUNNING / INTERRUPTED / FAILED`，没有「完成」。
- **停下后可以继续说**：`POST /api/tasks/{id}/messages` 沿同一 `thread_id` / checkpoint 续跑；`POST /api/tasks/{id}/resume` 保留给停止 / 崩溃后的续跑。
- **计划是一个文件**：计划模式下模型把计划写成 Markdown，系统存到任务目录的 `.qianfan/plans/<task_id>.md`；计划区显示原文、可整份编辑（`PUT /api/tasks/{id}/plan` 写回文件），用外部编辑器改同样生效。执行时计划以一条消息进入对话，下一条消息按文件当前内容跑。
- **危险动作**由设置里的 `policy_rules` + 一个钩子当场拒：不弹框、不进图重入；空表 = 不拦（不是安全边界，见下）。
- **断点续跑**用 `SqliteSaver` + `durability="sync"`；checkpoint 格式 v2，v1 旧库按迁移文档 §2.4 拒绝挂载。

## 工作区

- 在哪个目录敲 `qianfan`，新任务就在哪个目录读写、跑 bash、加载那里的 `AGENTS.md` 与 `.agents/skills`；页面地址带 `?dir=` 时以它为准（ADR 0010）。
- 历史列表默认只显示当前目录，可切「全部」；任务头标出目录。续说回到任务自己的目录；目录被删后只剩记录，续说返回 409。
- 这就是在你的项目里直接干活：bash 与写工具会改真实文件，**撤销靠 git**；在主目录或盘根启动不拦（见「这不是安全边界」）。
- MCP 服务仍是整个后台共用一份，工作目录是后台的启动目录，不随任务切换。

**一台机器只共用一个后台**（ADR 0010）：第二次在别的目录敲 `qianfan`，它会把浏览器开到已在跑的后台、定位到当前目录，然后退出，不另起进程（`--port` 只对第一次生效）。关掉启动它的终端 = 所有项目的任务停下，之后可以续说 / 续跑。`start.py` 的开发后台与 `qianfan` 互斥，一个在跑时另一个会拒绝启动。

## 能力

- **工具**：沙箱文件读写（`read` / `write` / `edit` / `ls` / `glob` / `grep`）、`bash`（工作区根、环境白名单、可配超时、停止收进程树含调用返回后的后台进程）、`web_search`（搜索源 `duckduckgo` / `serpapi` / `exa`；国内网络下 duckduckgo 可能不可达，设 `search_provider=exa` 并导出 `EXA_API_KEY`）、MCP。
- **子 agent**：`spawn_subagent` 单入口，`explore` / `execute` 两档，工具面只能收窄。
- **失败回传**：工具调用只执行一次（不自动重试、不熔断）；工具失败 / 被规则拒时，事件面与模型面各有一份可读原因。
- **上下文**：两层 AGENTS.md 注入、skills 清单、工具结果逐出、压缩。
- **工作区文件**：列出当前任务目录里按 `.gitignore` 可见的文件（bash 写的也在），文本预览、流式下载；**列表只是可见性过滤，preview / download 仍可点名被忽略的文件**；撤销靠 git。
- **观测**：SSE 事件流 + JSONL trace，前端可回放 / 导出。

## 模型

- 设置页管理多套「地址 + 模型 + Key」；预设只有 **DeepSeek**，其余用「自定义」；只支持 OpenAI 兼容接口。
- Key 两种来源：界面粘贴（存 `~/.qianfan/credentials.json`，仓库外）优先；其次配置里写的环境变量名（如 `LLM_API_KEY` / `DASHSCOPE_API_KEY`）。接口只回「已设 / 未设」与来源（粘贴 / 环境变量 / 无），不回 Key 本身。
- 新建任务默认用全局默认，创建前也可先选一套；任务里可中途换模型，下一条消息生效；子代理与压缩摘要跟随父任务的模型。
- 粘贴的 Key 是明文落盘，同账号的 bash 读得到；Coding Plan 条款通常不允许自建客户端调用，用「自定义」后果自担。

## 这不是安全边界

`qianfan` 只监听 `127.0.0.1`，Host 检查只认 `127.0.0.1` / `localhost`（挡 DNS 重绑定），
但这些都是**减少暴露面**，不是沙箱：

- **默认没有登录**：同一台机器上的其他进程 / 用户可以直接驱动 API；`auth_enabled` 只挡无凭据的访问，不是权限边界。远程访问不在本章范围。
- `bash` 拿到的是**运行账号的权限**：工作目录只是起点，`data/`（含 trace / checkpoint）、`.git`、`.env`、家目录与网络都可读写；trace 不是防篡改审计日志。撤销靠 git。
- 子进程只继承白名单环境变量；这挡的是「继承」，不是读文件（Linux 下 `/proc/$PPID/environ` 不在本章形态内）。
- **停止 / 超时**回收本次调用建立的进程组（POSIX）或 Job Object（Windows），包括调用已返回、任务随后被停止时的后台进程；用 `setsid` 等自行脱离的进程不受管。
- **只限时**（默认 30s，1–600s）并截断输出尾部（每流 30KB）；没有 CPU / 内存 / 磁盘 / 进程数上限。
- 需要真隔离就放进容器或虚拟机（本章不提供 Docker 启动方式）。

## 质量门禁

```bash
.\.venv311\Scripts\python.exe -m pip install -r requirements-dev.txt   # ruff / mypy / pre-commit
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy
cd frontend; npx tsc --noEmit; npm run check:sse; npm run build
```

提交前钩子每台机器装一次：`.\.venv311\Scripts\python.exe -m pre_commit install`。
CI 跑后端 lint / 类型 / 测试、前端类型 / 构建。发布前 / 换供应商：启服务在网页端手动跑一遍（不记模型与时间）。

## 文档

- 决策：[ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)（一条 loop + 计划 / 执行两模式）；[ADR 0009](docs/adr/0009-minimal-agent-chapter.md)（当前一章：最简 agent）；[ADR 0010](docs/adr/0010-launch-dir-workspace-and-model-profiles.md)（工作区 / 单后台 / 模型配置）
- 架构：[docs/architecture.md](docs/architecture.md)（现役；§3.3 REST / §3.4 SSE / §5 共享约定是契约面）；拓扑以 [graph.py](backend/core/agent/graph.py) 为准
- 切片历史：[docs/archive/pivot-plan-build.md](docs/archive/pivot-plan-build.md)
- 迁移：[docs/migration-langgraph-1x.md](docs/migration-langgraph-1x.md)
- 配置：[.env.example](.env.example)；工程约定：[AGENTS.md](AGENTS.md)；术语：[GLOSSARY.md](GLOSSARY.md)
- 历史：[docs/archive/OVERVIEW.md](docs/archive/OVERVIEW.md)（交付日志）

## License

MIT
