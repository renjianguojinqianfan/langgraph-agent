# Mission: 借 langgraph-agent 项目拿下校招/实习 Offer，并真正掌握 LangGraph 与 Agent 工程

## Why
用户是校招/实习求职者，简历核心项目是 langgraph-agent（LangGraph 自主任务 Agent 平台）。目标有二：(1) 面试中能自信、有深度地讲述本项目并扛住追问，拿到 Offer；(2) 借这个项目真正掌握 LangGraph 框架与 Agent 工程的通用知识——知识能迁移到任何框架、任何岗位，而不只是背下这一个仓库。

## Success looks like
- 能 3 分钟 STAR 讲清项目，并扛住高频追问：「为什么用 LangGraph 而不是自己写循环」「最大的技术挑战」「并发模型是什么」「LLM 应用怎么测试」
- 能白板画出 main 拓扑图（planner→executor→tool→reflect 循环 + 确认闸门），并解释每条条件边的路由逻辑
- 能徒手写出：纯 Python 最小 Agent 循环、一个 mini StateGraph（不查资料）
- 能讲清 LangGraph 核心机制：State/Node/Edge、reducer、superstep、checkpointer、interrupt
- 能把 P0 死循环修复与 P3「state 副本语义」发现讲成完整的工程故事（挑战→错误假设→根因→修复→验证）

## Constraints
- 中文教学；每周 7 小时以上，可安排密集课程线
- 起点：对项目有概念级了解但未深读 backend/ 源码；LangGraph 仅概念级
- 校招语境：面试官更看重基础扎实 + 学习能力 + 项目亮点叙事，不必追企业级运维深度

## Out of scope
- 前端 React 细节（面试叙事一句话带过即可）
- Kubernetes / 分布式部署等生产运维话题
- LangChain 全家桶其余组件（LCEL、旧版 AgentExecutor 等）
