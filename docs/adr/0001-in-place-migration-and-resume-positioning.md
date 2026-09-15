# 简历项目定位与 langgraph 迁移决策（grilling 总结）

> 2026-09-12 grilling 会话定居的决策树。本文件是跨会话交接的唯一权威来源；执行任何阶段前先读此文件。

## 1. 定居决策树

| # | 决策 | 定居值 |
|---|---|---|
| D1 | 项目定位 | 简历项目，唯一主打（深度线） |
| D2 | 目标读者 | AI/Agent 工程师方向 |
| D3 | 组合分工 | 本仓库扛 **agent 运行时深度**（断点续跑/熔断/风险闸/工具链/可观测）；`interview-agent-py` 辅证**工程落地**（PG/pgvector/Redis/MinIO/ADR/部署）。两仓库 README 互链并显式写分工 |
| D4 | 观看形态 | 只看仓库；不部署、不视频 |
| D5 | 时间约束 | 无硬约束；但首曝走闸门（D9） |
| D6 | 学习属性 | 保留；学习痕迹入仓库是加分项，前提是索引可读 |
| D7 | 本仓库动作 | **原地迁移 langgraph 0.2→1.2.x，不全量重写**；迁移内含"编排层按 1.x 范式重写" |
| D8 | 包装深度 | 全量包装；红线：不重写 git 历史 |
| D9 | 首曝闸门 | 动作集完成 + 回归全绿 + 讲解自检通过，仓库链接才上简历 |

## 2. D7 迁移内涵（决策完整，执行时不再二次决策）

1. 依赖：`langgraph` → 1.2.x LTS 线；`langgraph-checkpoint-sqlite` → 3.1.1（顺带闭环安全告警 #5/#6，dismiss 状态改"已修复"叙事）；删除 `langchain`/`langchain-openai`/`langchain-core` 三行死钉版（已验证 0 import）。
2. 编排层重写面（耦合面仅这 3 个生产文件）：`backend/core/agent/graph.py`、`backend/core/agent/subagent.py`、`backend/services/task_manager.py` 的图装配/checkpoint 用法按 1.x idioms 重写；显式采用一个 1.x 新特性（durability mode 或 typed streaming v2，二选一，spike 时按契合度定）。
3. 数据策略：旧 sqlite checkpoint 快照声明作废（纯运行时数据）；启动时检测旧格式给提示。
4. 闸门：351 离线测试全绿 + `scripts/live_e2e.py` 真实模型双场景 + `--check` 冒烟；迁移 commit 分阶段留痕（评估→依赖→编排重写→回归→采用新特性）。
5. 栈新鲜度补强：ruff + mypy 门禁进 CI（弥补周边工具链偏旧的短板）。
6. 硬规则不变：conftest 隔离、`_needs_confirm` 重算、resilience/registry 零改动（CI 守卫已机械化）。

## 3. 执行阶段（按序）

- **Phase 0 包装快修**（零风险，可立即）：修公开描述过期（331→351）；未跟踪杂物（`.mimosa/`、`MISSION.md`、`NOTES.md`、`RESOURCES.md`、`assets/`、`learning-records/` 等）逐个决定入库或 ignore；提交工作区遗留改动（Dockerfile/main.py/start.py 与 requirements.txt 注释）。
- **Phase 1 迁移 spike**：分支 → 依赖矩阵 → 编排层 1.x 重写 → 回归差异修复 → 新特性采用 → 全绿 → 合回 master。
- **Phase 2 叙事包装**：README 重写为叙事型（架构图/亮点/迁移章节/分工互链）；`learning-records/` 加索引 README；ruff/mypy 门禁落地。
- **Phase 3 首曝自检**：讲解深度审计（逐模块白板自讲：编排循环、resume 拒绝语义、熔断层、checkpoint 副本语义、孤儿对账、迁移差异）；简历 bullet 定稿。
- **闸门（D9）**：Phase 0–3 完成 + 全绿 + 自检过 → 链接上简历。

## 4. 被否决选项（留档，供面试追问"为什么"时引用）

- **全量重写**：首看可信度归零（薄 commit 图在 2026 年默认被怀疑 AI 生成）；叙事密度严格弱于迁移；P0–P3 教训无护栏重推导必丢；现代工具链信号已由 interview-agent-py 覆盖，重写无增量。
- **继续冻结**：栈信号随时间衰减，与简历目标冲突。
- **第三学习仓库**：仅当迁移完成后重写冲动仍强时的后备容器，且必须差异化定位（1.x 新范式专攻），避免同质化第三仓库。当前不执行。

## 5. 未决执行项（非设计决策，随阶段推进）

- 1.x 新特性二选一（durability mode vs typed streaming v2）：Phase 1 spike 时按与现有 SSE 可观测的契合度定。
- 讲解深度审计的具体补学清单：Phase 3 产出。
