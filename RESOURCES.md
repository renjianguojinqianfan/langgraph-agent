# LangGraph 与 Agent 工程学习资源

## Knowledge

### 官方文档（每个 API 断言的第一出处）
- [LangGraph Graph API overview — LangChain 官方文档](https://docs.langchain.com/oss/python/langgraph/graph-api)
  StateGraph 权威定义：nodes / edges / conditional edges / Send。用于：所有「框架原理」课的核心引用。
- [LangGraph Persistence — 官方文档](https://docs.langchain.com/oss/python/langgraph/persistence)
  Checkpointer 与 thread 级内存机制。用于：断点续跑课（对应项目 P3）。
- [LangGraph Interrupts — 官方文档](https://docs.langchain.com/oss/python/langgraph/interrupts)
  human-in-the-loop 的 `interrupt()` 机制，依赖 checkpointer。用于：确认闸门课，与项目自研闸门对比。
- [StateGraph 类 API Reference](https://reference.langchain.com/python/langgraph/graph/state/StateGraph)
  add_node / add_edge / add_conditional_edges / compile 的精确签名。
- [interrupt API Reference](https://reference.langchain.com/python/langgraph/types/interrupt)
  明确「interrupt 必须 mount checkpointer 才能工作」。

### 论文与工程方法论
- [ReAct: Synergizing Reasoning and Acting in Language Models (Yao et al., ICLR 2023)](https://arxiv.org/abs/2210.03629)
  Reason+Act 交替范式的原始论文（引用 1.4 万+）。用于：Agent 主循环课，回答「你的循环和 ReAct 什么关系」。
  - [ReAct 项目页（含代码与 demo）](https://react-lm.github.io/)
- [Building Effective Agents — Anthropic Engineering (2024-12)](https://www.anthropic.com/engineering/building-effective-agents)
  workflows vs agents 之分、五种组合模式、agent 循环 + 停止条件、「能简单就别复杂」。用于：所有设计权衡类面试题的权威背书。

### MCP（模型上下文协议）
- [MCP 官方文档 — Introduction](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro)
  「AI 应用的 USB-C 口」。用于：MCP 课与项目 `core/mcp/` 对照。
- [MCP Specification](https://modelcontextprotocol.io/specification/2026-07-28)
  协议正典：initialize / list_tools / call_tool / transports。

### 一手教材（本项目自身——最重要的资源）
- `backend/core/agent/graph.py` — 全项目唯一一张图，110 行，条件边活教材
- `backend/core/agent/state.py` — AgentState TypedDict，状态建模教材
- `backend/core/agent/nodes.py` — 六个节点的真实实现
- `backend/core/tools/resilience.py` — 熔断/重试/ToolExecutor
- `backend/services/task_manager.py` — resume / 孤儿对账 / 权威停止信号
- `docs/architecture.md` 与 `docs/incremental-arch-p0..p3.md` — 项目自带架构文档（含每轮迭代的「为什么」）
- `OVERVIEW.md` — 五轮迭代交付全记录（面试叙事的素材矿）

## Wisdom (Communities)

- [LangChain Forum](https://forum.langchain.com/)
  LangGraph 官方论坛，框架作者出没。用于：框架行为的疑难杂症（如 interrupt 被 ignore、条件边不生效）。
- [r/LangChain](https://reddit.com/r/LangChain)
  高活跃度实践者社区。用于：看别人怎么用 LangGraph 踩坑。
- [牛客网](https://www.nowcoder.com/)
  中文校招面经主阵地。用于：搜集 AI 应用/后端岗位真实面经，反哺课程题库。

## Gaps

- DeepAgents（langchain-ai/deepagents，项目 P0 对标物之一）的官方仓库与文档 URL 待核实后补入
- 中文世界的 LangGraph 深度教程尚未找到高信任源，暂以官方文档英文原版为准
