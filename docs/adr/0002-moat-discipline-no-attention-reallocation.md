---
status: accepted
---

# 护城河纪律：不为凑功能挪用深度能力的注意力

> 2026-09-16 wayfinder map #27 / ticket #30 grilling 定居；推导依据见
> [capability §五 结论 4](../capability-first-principles.md)，
> 执行纪律见 [roadmap §〇/§五](../roadmap-pawbench.md)。

## 决策

本仓库的 Tier 1 深度能力（现列三项：**T1.5 安全预防面、T1.6 持久化恢复、
T1.3 循环控制韧性**；细目以 [capability §四](../capability-first-principles.md)
对照表为准。**开放式清单**——后续交付新的 Tier 1 深度能力（如 P1-B 回滚）即自动入列）
是反超两个开源标杆的护城河，其验收走**离线测试全量 + live 双场景，不走评测分**。
任何后续规划（P1-A′ / P1-B / M2 及以后）不得为凑功能或追分数，挪用维护这些能力的注意力。

为什么值得 ADR 级冻结：注意力是本仓库唯一真约束（AI 代写使时间无限）；评测分只验收
新能力，护城河深度的退化在两道既有闸门上**不可见**（测试照绿、live 照过），掉了没人补
——这是注意力层的难逆转侵蚀，CI guard（管代码层零改动）覆盖不到。

## 「挪用」的操作定义（三不）

1. **不削弱语义换便利**：不为实现新功能简化三项能力的行为语义
   （如放宽 resume 拒绝、绕过确认闸门、缩短熔断退避）。
2. **不降验收与 review 力度**：涉及这些区域的改动照走逐行深 review + live 双场景
   验收，不因赶 spec 进度降级。
3. **不让位于评测分导向的重构**：不为评测分数改变这些能力的实现取舍。

## 边界与例外

- 受保护文件（`resilience.py` / `registry.py` / `nodes.py` 的 `_needs_confirm` 重算块）
  零改动约束**独立于本 ADR**，由 CI guard 机械化拦截——本 ADR 管的是注意力层。
- spec 执行期的回归停线钩子（spec 验收见 P0 回归 → 该 spec 立即停线）见
  [#29 standing rule](https://github.com/renjianguojinqianfan/langgraph-agent/issues/29)，
  本 ADR 不复制。
- 实证矛盾时走**记录修正**：ticket resolution comment 记录 + 本文件同步修正
  （同 roadmap §一 机制，不叫重开）。

## 文档分工（防漂移）

决策记录（本文件）→ 推导与能力细目（capability §四/§五）→ 执行纪律
（roadmap §〇/§五）。三处职能不同，以指针衔接，不复制对方内容。

## 修正记录

（暂无——每次修正须先在对应 ticket 的 resolution comment 留痕，再同步本文件）
