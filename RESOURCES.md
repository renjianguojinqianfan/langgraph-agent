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
- [Effective context engineering for AI agents — Anthropic Engineering (2025-09-29)](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  context rot（窗口内 token 增多则取回能力下降，成因为注意力 n² 两两关系，属性能梯度而非硬悬崖）、
  注意力预算、compaction、结构化笔记、子 agent 架构。用于：课程 02「上下文预算与压缩」的核心引用，
  兼作「为什么长上下文会退化」与「压缩后附最近访问的 5 个文件」的出处。

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

### 本机笔记库 · agent 模式库（跨工具的工程模式卡）

讲义知识源之一。**讲义引用卡片、不复制卡片内容**：正文只取教学所需的最小对比，完整方案空间、
取舍与「何时不用」通过链接与本地路径指向卡片。

- `E:\笔记\02_沉淀库\C专业知识\C5 Agent研究\agent模式库\README.md` — 索引、七段卡片规范、三层分工、样本清单（含 pin）
- `E:\笔记\02_沉淀库\C专业知识\C5 Agent研究\agent模式库\20260921_C5_上下文预算与压缩.md` — 模式卡 02，课程 02 的知识源
- `E:\笔记\02_沉淀库\C专业知识\C5 Agent研究\agent模式库\20260921_C5_工具暴露与两阶段路由.md` — 模式卡 01，可对接后续工具层课程
- `E:\笔记\02_沉淀库\C专业知识\C5 Agent研究\20260921_C5_Agent与AI产品岗面试真题分布_网络检索.md` — 真题密度排序，决定课程深度的优先级

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
