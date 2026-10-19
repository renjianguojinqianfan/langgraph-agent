# LangGraph 自主任务 Agent

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → **一条 loop、计划 / 执行两模式** → 工具调用 → 停下时人看产物；FastAPI + React 前后端一体，SSE 实时可视化 + Trace 回放。

重点是运行时：多轮续说、checkpoint、模式切换、危险动作拦截、工具生态与可观测性，而不是一个 demo 界面。

**离线测试全绿**（零网络、零 Key、MockLLM）· CI 两个 job · 发布前在网页端跑一次真实模型。

## 快速开始

日常使用（仓库外任意目录都能敲 `qianfan`；后端与网页同源起在 `127.0.0.1`）：

```bash
uv tool install -e . --overrides overrides.txt   # 每台机器一次
cd frontend && npm ci && npm run build           # 前端产物；改前端后重跑
cp .env.example .env        # LLM_API_KEY 走环境变量；或 use_mock_llm=true 离线
qianfan                                          # 自动开浏览器；--port / --no-browser 可选
```

`overrides.txt` 只做一件事：mcp 声明 `uvicorn>=0.31.1`，而本仓钉 `uvicorn<0.31`；
override 让干净环境装出与 `requirements.txt` 一致的版本。缺 `frontend/dist` 时
`qianfan` 会提示先打包。

改前端时用开发启动（Vite 热重载，后端 `--reload`）：

```bash
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
- **计划可改**：停下时 `PUT /api/tasks/{id}/plan`，下一条消息按新计划跑。
- **危险动作**由设置里的 `policy_rules` + 一个钩子当场拒：不弹框、不进图重入；空表 = 不拦（不是安全边界，见下）。
- **断点续跑**用 `SqliteSaver` + `durability="sync"`；checkpoint 格式 v2，v1 旧库按迁移文档 §2.4 拒绝挂载。

## 能力

- **工具**：沙箱文件读写（`read` / `write` / `edit` / `ls` / `glob` / `grep`）、`bash`（工作区根、环境白名单、可配超时、停止杀进程树）、`web_search`、MCP。
- **子 agent**：`spawn_subagent` 单入口，`explore` / `execute` 两档，工具面只能收窄。
- **失败回传**：工具调用只执行一次（不自动重试、不熔断）；工具失败 / 被规则拒时，事件面与模型面各有一份可读原因。
- **上下文**：两层 AGENTS.md 注入、skills 清单、工具结果逐出、压缩。
- **回滚**：`write` / `edit` 写入前拍 before-image，`POST /api/tasks/{id}/rollback` 按账本逆序恢复（只动文件、不动 checkpoint；bash 写的文件不进账本）。
- **观测**：SSE 事件流 + JSONL trace，前端可回放 / 导出。

## 这不是安全边界

本章把监听与 Host 检查收在本机（`127.0.0.1` / `localhost`），但那是**减少暴露面**，不是沙箱：

- `policy_rules` 默认是**空表**——默认什么都不拦。
- `bash` 拿到的是**运行账号的权限**，与你在终端里敲命令没有区别；工作目录（工作区根）只是起点，不是围墙，`cd ..`、绝对路径、网络都可达。
- 子进程只继承白名单环境变量，API Key 不会漏进去；这减少泄露，不是权限隔离。
- 需要真隔离就把整个服务放进容器或虚拟机。

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

- 决策：[ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)（一条 loop + 计划 / 执行两模式）；[ADR 0009](docs/adr/0009-minimal-agent-chapter.md)（当前一章：最简 agent）
- 架构（v0.1 历史档；§5 共享约定仍现役）：[docs/architecture.md](docs/architecture.md)；拓扑以 [graph.py](backend/core/agent/graph.py) 为准
- 切片历史：[docs/pivot-plan-build.md](docs/pivot-plan-build.md)
- 迁移：[docs/migration-langgraph-1x.md](docs/migration-langgraph-1x.md)
- 配置：[.env.example](.env.example)；工程约定：[AGENTS.md](AGENTS.md)；术语：[GLOSSARY.md](GLOSSARY.md)
- 历史：[OVERVIEW.md](OVERVIEW.md)（交付日志）

## License

MIT
