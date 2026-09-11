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
7. 上下文工程：token 预算与压缩（context.py）
8. 工具生态外扩：MCP 客户端与 Git 工具（task_manager 显式加载、不经 @register 的原因）
9. 多 Agent 协作与 RAG：子任务隔离、消息折叠、最小 KB

**主线 C+D · 面试冲刺**
10. STAR 项目叙事：两个核心故事（P0 死循环修复 / P3 副本语义发现）+ 简历表述打磨
11. 高频追问题库：为什么 LangGraph、351 测试与 Mock LLM 离线隔离、并发模型、recursion_limit 修复
12. 手写代码实战：徒手写纯 Python Agent 循环 + mini StateGraph

节奏：每周 2-3 课，穿插「前课混测」（interleaving）。每课结束留下检索练习作业。

## 教学偏好观察
- （待积累：讲多细、喜欢什么类型的练习、对英文材料的接受度）

## 工作区说明
- teach 状态文件位于仓库根目录（MISSION.md / lessons/ / reference/ / assets/ / learning-records/ / NOTES.md / RESOURCES.md），**已入库**（2026-09-12 杂物 triage 决定：学习痕迹是加分项，前提是索引可读）；入口索引见 `learning-records/README.md`
- 工具态产物不入库：`.mimosa/`、`.qoder/better-harness/`、`_write_bh_report.py` 已在 .gitignore
- 项目硬规则照常生效：不碰 conftest 隔离、`_needs_confirm` 重算逻辑、resilience/registry（教学只读不写）
