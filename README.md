# LangGraph 自主任务 Agent

基于 **LangGraph 1.2.x（StateGraph）** 的自主任务 Agent 平台：自然语言任务 → **一条 loop、计划 / 执行两模式** → 工具调用 → 停下时人看产物；FastAPI + React 前后端一体，SSE 实时可视化 + Trace 回放。

重点是运行时：多轮续说、checkpoint、模式切换、危险动作拦截、工具生态与可观测性，而不是一个 demo 界面。

**离线测试全绿**（零网络、零 Key、MockLLM）· CI 三 job · 发布前在网页端跑一次真实模型。

## 快速开始

```bash
python -m venv .venv311 && source .venv311/bin/activate   # Windows: .venv311\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # LLM_API_KEY 走环境变量；或 use_mock_llm=true 离线
uvicorn backend.main:app --port 8000 --reload
cd frontend && npm install && npm run dev   # http://localhost:5173
```

一键：`python start.py --mock` / `python start.py` / `docker compose up --build`。
虚拟环境请叫 `.venv311`（`start.py` 会拦住根目录的 `.venv`）。

## 核心模型

一个任务 = 一段多轮对话：

```text
agent ──有 tool call──> tool ──> agent
  └── 本轮最后一次调用没有 tool call ──> finish（停下，INTERRUPTED）
```

- **一条 loop、一个 `agent` 节点**；模式只换 prompt 和工具面：`plan` = 只读 `explore` 档，`build` = 全部已挂载工具。默认 `build`，开关黏性。
- **停下 = 这一轮的最后一次 `agent` 调用没有工具调用**；状态只有 `RUNNING / INTERRUPTED / FAILED`，没有「完成」。
- **停下后可以继续说**：`POST /api/tasks/{id}/messages` 沿同一 `thread_id` / checkpoint 续跑；`POST /api/tasks/{id}/resume` 保留给停止 / 崩溃后的续跑。
- **计划可改**：停下时 `PUT /api/tasks/{id}/plan`，下一条消息按新计划跑；「重新规划」是借一轮 `plan` 模式的动作，不改黏性开关。
- **危险动作**由设置里的 `policy_rules` + 一个钩子当场拒：不弹框、不进图重入；空表 = 不拦。
- **断点续跑**用 `SqliteSaver` + `durability="sync"`；checkpoint 格式 v2，v1 旧库按迁移文档 §2.4 拒绝挂载。

## 能力

- **工具**：沙箱文件读写、代码执行、web 搜索、HTTP、Git、MCP、OpenAPI、插件。
- **子 agent**：`spawn_subagent` 单入口，`explore` / `execute` 两档，工具面只能收窄。
- **韧性**：熔断 + 退避重试；工具失败 / 被规则拒时，事件面与模型面各有一份可读原因。
- **上下文**：两层 AGENTS.md 注入、skills 清单、工具结果逐出、压缩与知识库。
- **回滚**：沙箱写入前拍 before-image，`POST /api/tasks/{id}/rollback` 按账本逆序恢复（只动文件、不动 checkpoint）。
- **观测**：SSE 事件流 + JSONL trace，前端可回放 / 导出。

## 质量门禁

```bash
.\.venv311\Scripts\python.exe -m pip install -r requirements-dev.txt   # ruff / mypy / pre-commit
.\.venv311\Scripts\python.exe -m pytest backend/tests/ -q
.\.venv311\Scripts\python.exe -m ruff check backend scripts
.\.venv311\Scripts\python.exe -m mypy
cd frontend; npx tsc --noEmit; npm run check:sse; npm run build
```

提交前钩子每台机器装一次：`.\.venv311\Scripts\python.exe -m pre_commit install`。
CI 跑后端 lint / 类型 / 测试、前端类型 / 构建、Docker 构建。发布前 / 换供应商：启服务在网页端手动跑一遍（不记模型与时间）。

## 文档

- 决策：[ADR 0008](docs/adr/0008-single-loop-plan-build-modes.md)（一条 loop + 计划 / 执行两模式）；[ADR 0009](docs/adr/0009-minimal-agent-chapter.md)（当前一章：最简 agent）
- 架构（v0.1 历史档；§5 共享约定仍现役）：[docs/architecture.md](docs/architecture.md)；拓扑以 [graph.py](backend/core/agent/graph.py) 为准
- 切片历史：[docs/pivot-plan-build.md](docs/pivot-plan-build.md)
- 迁移：[docs/migration-langgraph-1x.md](docs/migration-langgraph-1x.md)
- 配置：[.env.example](.env.example)；工程约定：[AGENTS.md](AGENTS.md)；术语：[GLOSSARY.md](GLOSSARY.md)
- 历史：[OVERVIEW.md](OVERVIEW.md)（交付日志）

## License

MIT
