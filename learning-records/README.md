# learning-records · 学习痕迹索引

本目录记录 langgraph-agent 作为**学习项目**时的成长轨迹：每个阶段留下一条编号
记录，写清「学了什么机制 → 落回仓库里哪段真实代码 → 还没搞懂什么」。它不是
文档站，而是学习过程的原始凭证——被追问「你是怎么学会 LangGraph 的」时，这里
就是答案。

工程侧的权威文档在别处：架构看 [`docs/architecture.md`](../docs/architecture.md)，
每轮迭代的取舍看 [`OVERVIEW.md`](../OVERVIEW.md)，改代码前必读
[`AGENTS.md`](../AGENTS.md)。本目录只放**学习者视角**的痕迹。

## 记录编号约定

`NNNN-短标题.md`：四位序号从 0001 递增，一个阶段一条；既有记录不重写，认知被
推翻时在新记录里纠正旧的（保留「当时怎么想的」正是痕迹的价值）。

| 编号 | 主题 | 阶段 | 关联代码 |
|---|---|---|---|
| [0001](0001-initial-profile.md) | 初始画像与使命确立 | 开课前的定位 | — |

## 教学工作区其余文件

学习痕迹不止在本目录，仓库根目录下这几处同属一套（`teach` 工作流的状态文件），
一并索引在此：

| 路径 | 作用 |
|---|---|
| [`MISSION.md`](../MISSION.md) | 使命：为什么学、学成什么样算成功、明确不学什么 |
| [`NOTES.md`](../NOTES.md) | 12 课主线规划（A 框架原理 / B Agent 工程 / C+D 面试冲刺）与滚动调整 |
| [`RESOURCES.md`](../RESOURCES.md) | 资源清单：官方文档、论文，以及本项目自身的一手教材 |
| [`lessons/`](../lessons/) | 每课的可交互讲义（HTML，含小测） |
| [`reference/`](../reference/) | 速查卡（如 StateGraph 核心概念） |
| [`assets/`](../assets/) | 讲义共用的样式与小测脚本 |

## 进度快照

- ✅ 课程 01 · LangGraph 心智模型（State / Node / Edge / Conditional Edge，用
  [`backend/core/agent/graph.py`](../backend/core/agent/graph.py) 的真代码讲）
  → 讲义 [`lessons/0001-langgraph-mental-model.html`](../lessons/0001-langgraph-mental-model.html)，
  速查卡 [`reference/stategraph-cheatsheet.html`](../reference/stategraph-cheatsheet.html)
- ✅ 课程 02 · 上下文预算与压缩（对应主线 B 第 7 课，用
  [`backend/core/agent/context.py`](../backend/core/agent/context.py) 的两档实现讲；
  知识源之一是本机笔记库的模式卡 02）
  → 讲义 [`lessons/0002-context-budget-and-compaction.html`](../lessons/0002-context-budget-and-compaction.html)，
  速查卡 [`reference/context-budget-cheatsheet.html`](../reference/context-budget-cheatsheet.html)
- ⬜ 课程 03 起 → 规划见 [`NOTES.md`](../NOTES.md)

> 讲义编号按产出顺序递增，与课程编号解耦。课程 02 的讲义对应课程主线里的第 7 课，
> 跳课原因记在 [`NOTES.md`](../NOTES.md) 的「节奏与跳课记录」。
