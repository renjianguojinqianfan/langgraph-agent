# 教学笔记（教师私用草稿本）

## 用户画像
- 校招/实习求职者，简历主打本项目 langgraph-agent
- 中文教学；每周可投入 7 小时以上，接受密集节奏
- 起点（2026-08-28 问卷）：项目概念级了解、未深读 backend/ 源码；LangGraph 仅概念级
- 目标四能力全选：项目面试叙事 / Agent 工程通用模式 / LangGraph 框架原理 / 手写代码能力

## 课程主线（12 课规划，随学习记录滚动调整）

**主线 A · LangGraph 框架原理**
1. ✅ LangGraph 心智模型：State/Node/Edge/Conditional Edge（用 graph.py 真代码）
2. 状态更新机制：节点返回「部分 state」、reducer、superstep——为 P3 副本语义埋伏笔
3. human-in-the-loop：项目自研确认闸门 vs 官方 interrupt()（为何官方方案依赖 checkpointer）
4. Checkpointer 断点续跑：thread_id / SqliteSaver / P3「每 superstep 传 state 副本」大坑（最佳面试故事）

**主线 B · Agent 工程通用模式**
5. Agent 主循环与 ReAct：planner→executor→tool→reflect 对照 ReAct 论文与 Anthropic agents 文章
6. 工具层工程：BaseTool 规范、注册表、熔断+指数退避（resilience.py）
7. ✅ 上下文工程：token 预算与压缩（context.py）→ 讲义 [`lessons/0002-context-budget-and-compaction.html`](lessons/0002-context-budget-and-compaction.html) + 速查卡 [`reference/context-budget-cheatsheet.html`](reference/context-budget-cheatsheet.html)
8. 工具生态外扩：MCP 客户端与 Git 工具（task_manager 显式加载、不经 @register 的原因）
9. 多 Agent 协作与 RAG：子任务隔离、消息折叠、最小 KB

**主线 C+D · 面试冲刺**
10. STAR 项目叙事：两个核心故事（P0 死循环修复 / P3 副本语义发现）+ 简历表述打磨
11. 高频追问题库：为什么 LangGraph、离线全量测试与 Mock LLM 离线隔离（计数以实跑为准，不背数字）、并发模型、recursion_limit 修复
12. 手写代码实战：徒手写纯 Python Agent 循环 + mini StateGraph

## 节奏与跳课记录

- 节奏：每周 2-3 课，穿插「前课混测」（interleaving）。每课结束留下检索练习作业。
- **2026-09-21 跳课说明**：课程 02 的讲义按主线 B 第 7 课（上下文工程）交付，而非主线 A 第 2 课
  （状态更新机制）。原因是笔记库的模式卡 02 刚定稿，知识源齐全、可直接接进课程；
  主线 A 第 2 课仍待做，它同时是课程 04「断点续跑大坑」的前置，不应被长期推后。
  本次讲义编号为 `0002`（按讲义产出顺序递增，与课程编号解耦）。

## 知识层对接（2026-09-21 起）

讲义的知识源之一是本机笔记库的 agent 模式库。约定：**讲义引用卡片，不复制卡片内容**——
正文只取教学所需的最小对比，完整方案空间、取舍与「何时不用」通过链接与本地路径指向卡片。

| 讲义 | 引用的卡片 |
|---|---|
| 课程 02 | `E:\笔记\02_沉淀库\C专业知识\C5 Agent研究\agent模式库\20260921_C5_上下文预算与压缩.md`（模式卡 02） |

后续若做课程 06（工具层工程）与课程 08（工具生态），可分别对接模式卡 01
（`20260921_C5_工具暴露与两阶段路由.md`）与样本档。

## 讲义时效与复核记录（2026-09-25 起）

讲义与速查卡的页头固定声明一行 `来源 pin：langgraph-agent@<sha>（复核 <日期>）`，指向本仓库提交。
判定与核实工具在笔记库：`.workbuddy/check_repos.py` 与
`C5 Agent研究/agent模式库/20260925_C5_外部仓库与讲义时效机制_自整理.md`。

- **红** = pin 之后有提交触及讲义引用的源码位置，必须先看一眼；**黄** = 有提交但未触及引用位置；**绿** = pin 与 HEAD 一致。
- **红档不等于必须改稿**。复核结论可以是「无涉」，此时只升 pin 与复核日期，正文不动。
- 复核记录追加在下表，一行一次，不覆盖历史。

| 日期 | 对象 | pin 变化 | 结论 | 依据 |
|---|---|---|---|---|
| 2026-09-25 | 课程 02 + 参考 02 | `eaef5d5` → `f78812d7` | 无涉，正文未改 | 23 个提交 / 31 个文件，命中 `backend/config.py`；该文件唯一改动是新增 `llm_request_timeout_sec = 90`（#13 单次请求时限），与课程主题无关。讲义引用的 9 个 `context_*` 参数（32000 / 10 / 0 / truncate / 300 / 4000 / 10 / 800 / 400）与第 64 行注释逐条比对未变 |

## 教学偏好观察
- （待积累：讲多细、喜欢什么类型的练习、对英文材料的接受度）

## 工作区说明
- teach 状态文件位于仓库根目录（MISSION.md / lessons/ / reference/ / assets/ / learning-records/ / NOTES.md / RESOURCES.md），**已入库**（2026-09-12 杂物 triage 决定：学习痕迹是加分项，前提是索引可读）；入口索引见 `learning-records/README.md`
- 工具态产物不入库：`.mimosa/`、`.qoder/`（2026-09-15 起整体 ignore；原 `.qoder/specs/` 已迁 `docs/specs/` + `docs/adr/`）、`_write_bh_report.py` 已在 .gitignore；文档归属约定见 `AGENTS.md` §3
- 项目硬规则照常生效：不碰 conftest 隔离、`_needs_confirm` 重算逻辑、resilience/registry（教学只读不写）
