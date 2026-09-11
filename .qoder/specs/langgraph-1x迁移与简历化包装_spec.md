# Spec：langgraph 0.2 → 1.2.x 原地迁移与简历化包装（Phase 0–2）

> 决策来源：`.qoder/specs/简历项目定位与langgraph迁移决策_grilling总结.md`（D1–D9，2026-09-12 grilling 定居）。本 spec 决策完整，执行时不做二次决策。

## Problem Statement

简历主打仓库运行在 langgraph 0.2（2024 API 线），而 2026 年当前是 1.2.x LTS 线：栈信号过期削弱简历价值；两条安全公告（CVE-2025-67644 / CVE-2026-71433）因 3.x 破坏 resume serde 兼容而被钉版接受；公开仓库描述与测试数已过期；工作区存在未提交遗留与未跟踪杂物。全量重写已被否决（首看可信度、叙事密度、教训回归三条硬理由），需要一份 agent 可直接执行的原地迁移方案。

## Solution

在 351 测试回归网保护下原地迁移到 langgraph 1.2.x：编排层按 1.x 范式重写并显式采用一个 1.x 新特性；checkpoint-sqlite 升至 3.1.1 闭环安全公告；旧 checkpoint 快照作废并有启动检测；CI 补 ruff/mypy 门禁补强栈信号；同步完成简历化包装（描述修正、杂物 triage、叙事型 README、learning-records 索引）。迁移 commit 分阶段留痕，作为简历叙事资产。

## User Stories

1. As a 简历所有者, I want 仓库运行在 langgraph 1.2.x LTS 线, so that 首次观感时栈信号读作"当前"而非"停更"。
2. As a 简历所有者, I want 迁移以分阶段 commit 留痕（评估→依赖→编排重写→回归→新特性→包装）, so that 历史里有一章可读的迁移叙事。
3. As a 面试官, I want README 有迁移章节（为什么迁、怎么迁、代价与闸门）, so that 我能快速评估工程判断力。
4. As a 维护者, I want 迁移后 351 离线测试全绿, so that 行为不变性被证明而非被声称。
5. As a 维护者, I want resume/checkpoint 语义在新 checkpointer 上被专项复验, so that P3 断点续跑契约不回归。
6. As a 安全审查者, I want checkpoint-sqlite 处于 3.1.1, so that 两条 CVE 从"已接受"变为"已修复"。
7. As a 维护者, I want 三行死 langchain 钉版被删除, so that 依赖面诚实且升级敌对钉版减少。
8. As a 维护者, I want 旧格式 checkpoint 快照在启动时被检测、告警并拒绝挂载, so that 升级永不静默腐蚀 resume 状态。
9. As a 学习者, I want 编排层按 1.x idioms 重写, so that 代码库教授当前框架实践而非 2024 实践。
10. As a 简历所有者, I want 恰好一个 1.x 新特性被显式采用并在 README 点名, so that 简历能写"跟进框架演进"而不只"跑在新版上"。
11. As a CI 消费者, I want ruff + mypy 门禁进入 workflow, so that 工具链新鲜度信号可见且代码质量有闸。
12. As a 简历所有者, I want 公开仓库描述修正（测试数过期）, so that 首看时描述与 README 不互相打脸。
13. As a 面试官, I want 未跟踪杂物逐个被 triage（入库带索引或 ignore）, so that 仓库读起来是刻意的而非散乱的。
14. As a 简历所有者, I want learning-records 有索引 README, so that 学习痕迹读作成长脉络而非噪音。
15. As a 简历所有者, I want README 与 interview-agent-py 互链并显式写分工, so that 组合读作互补而非重复。
16. As a agent 执行者, I want 硬规则（conftest 隔离、_needs_confirm 重算、resilience/registry 零改动）保持不变, so that 受保护行为不回归且 CI 守卫继续有效。
17. As a 维护者, I want 迁移后真实模型 live_e2e 双场景 PASS, so that 离线确定性与真实行为双重成立。
18. As a 维护者, I want --check 离线冒烟在 CI 继续通过, so that 无 Key 环境也能抓住接线回归。
19. As a 面试官, I want 被 dismiss 的安全公告在迁移章节被记录为"已随迁移修复", so that 安全故事闭环。
20. As a 简历所有者, I want git 历史不被重写, so that 迭代痕迹的可信度资产完整保留。
21. As a 维护者, I want 工作区遗留改动（安全加固与解释器检查）先于迁移提交, so that 迁移 diff 不混入无关变更。
22. As a 面试官, I want 仓库根目录有 .python-version 与质量门禁配置, so that 环境可复现性一眼可见。

## Implementation Decisions

- **重写面限定**：仅编排层三个模块角色——图装配（graph 构建/compile）、子代理执行器的图调用、任务管理器的 checkpointer 挂载与 invoke/get_state。工具层、熔断层、registry、API 层、前端一律不碰。
- **依赖矩阵**：langgraph → 1.2.x LTS 线；langgraph-checkpoint-sqlite → 3.1.1；删除 langchain / langchain-openai / langchain-core 三行死钉版（已验证 0 import）；mcp 维持 `--no-deps` 安装约束；uvicorn/starlette 版本区间不动。
- **1.x 新特性选择规则（决策完整）**：优先 **durability mode**（与现有 invoke 式执行 + checkpoint resume 叙事天然契合、不改前端契约）；仅当 spike 证明 typed streaming v2 能被现有 SSE 事件总线消费且零前端契约变更时才改选它；最终选择写入 README 迁移章节。
- **checkpoint 数据策略**：旧 sqlite 快照声明作废（纯运行时数据，不入库）；启动时检测旧格式 → 日志告警 + 拒绝挂载 + 提示文案指向文档；不做自动迁移。
- **安全公告叙事**：#5/#6 维持 dismissed 状态不重开；requirements.txt 注释与 README 迁移章节记录"已随 3.1.1 修复"。
- **CI 补强**：backend-test job 内新增 ruff check + mypy 步骤（合入时基线必须全净，不留 ignore 债）；guard-protected-files job 与 conftest 隔离块一字不动。
- **commit 分阶段**：Phase 0 遗留提交 → 依赖矩阵 → 编排层 1.x 重写 → 回归差异修复 → 新特性采用 → 包装/文档，每阶段独立 commit。
- **杂物 triage 规则**：入库 = `.python-version`、学习性文档（MISSION/NOTES/RESOURCES/learning-records/lessons/assets）、`.qoder/specs/`；ignore = `.mimosa/`、`_write_bh_report.py` 等工具态产物；learning-records 加索引 README。
- **README 叙事结构**：定位与亮点 → 架构图 → 迁移章节（动机/矩阵/代价/闸门/安全闭环）→ 组合分工互链（interview-agent-py）→ 运行与质量门禁说明。
- **公开描述修正**：经 repo 编辑接口更新 description（测试数与能力概述以迁移后实况为准）。
- **硬规则不变**：P0 死循环修复逻辑、resume 拒绝语义、熔断层零改动、测试环境隔离块全部保留。

## Testing Decisions

- **好测试的标准**：只断言外部行为（任务生命周期状态、事件流类型、resume 结果、artifact 落盘），不断言 langgraph 内部 API 调用形态；迁移差异修复不得靠改测试迁就实现。
- **主缝**：现有 351 离线套件（最高行为缝）；迁移后必须全绿，用例数变化需在 commit 信息说明。
- **新增用例位置**：旧格式 checkpoint 检测用例进现有 checkpointer 测试模块（fixture 为旧格式 sqlite 文件）；不新建测试文件、不开新缝。
- **次缝**：`scripts/live_e2e.py --check`（离线接线冒烟）+ 真实模型双场景（AGENTS.md 完成定义）。
- **强制缝**：CI workflow 运行（含新增 ruff/mypy 步骤与既有 guard job）。
- **先例**：test_checkpointer.py / test_resume.py / test_orphan_reconcile.py（P3 缝用法）、task-08c 的 --check 模式。

## Out of Scope

- Phase 3 人工项：讲解深度审计、简历 bullet 定稿、首曝闸门放行（D9，人持有）。
- 前端任何改动；除选定 1.x 特性外的新功能。
- git 历史重写（rebase/filter）。
- 第三学习仓库（后备选项，当前不执行）。
- interview-agent-py 仓库内的改动（互链文案在本仓库 README 单侧先写）。
- Dependabot 配置与 CI 告警门禁机制（此前讨论的"第三梯队"可选项，另立 spec）。

## Further Notes

- 本 spec 的每条决策可追溯至 grilling 总结 D1–D9；被否决选项（全量重写/继续冻结/第三仓库）的理由留档于该文件第 4 节，供面试追问引用。
- 迁移本身是简历叙事资产：commit 分阶段与 README 迁移章节是刻意设计，不是副产品。
- 执行顺序硬约束：Phase 0（遗留提交 + 描述修正 + 杂物 triage）必须先于迁移 commit，保证迁移 diff 纯净。
