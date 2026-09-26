# Implementation Workflow（新票实施分工）

> 单人 + 多工具的**实施/评审分离**制：qoder 写、opencode 审。
> 触发：任何带 `ready-for-agent` 的实施票。规则冲突时以 `AGENTS.md`「跨任务安全边界」优先。

## 流程（七步）

| 步 | 执行者 | 动作 |
|---|---|---|
| 1 | **qoder** | 从 `master` 切 feature 分支（`feat/<slug>` 或 `fix/<slug>`，票号写进 PR）；读票面 + AGENTS.md 任务路由指定的权威文档 |
| 2 | **qoder** | 调用 `implement` skill 实施（TDD：约定的 seam 上先写红测试；常跑类型检查与单测） |
| 3 | **qoder** | 调用 `code-review` skill **自评审**（双轴：规范轴 + 需求轴），返工发现后才进下一步 |
| 3.5 | **qoder** | 调用 `neat-freak` skill 做**知识与文档收尾**（在提 PR 之前）：能力面 / 接口 / 配置语义变了，必须同步 `README.md`、`.env.example` 与对应架构文档；顺带清掉**本次自造的孤儿**（引导性 prompt、失同步的注释、失去消费者的配置键与形参）。历史交付快照（`docs/incremental-prd-*.md`、mermaid 交付图、`lessons/`、`reference/`）不改，改在权威段声明「以本节为准」 |
| 4 | **qoder** | 提交 PR：分阶段 commit **不 squash**；标题带票号（`Closes #N`）；验证状态四态如实写（通过/跳过/未运行/产物验收） |
| 5 | **opencode** | 以评审者身份独立评 PR：双轴评审 + 门禁核对（ruff/mypy/pytest/`--check`）+ 冻结区检查 + live 视票面需要；**只提意见与返工要求，不直接替改** |
| 6 | 评审侧/用户 | 合并：CI 全绿 + opencode 评审通过 → merge commit（不 squash） |

## 边界

- **实施者不合并自己的 PR**——合并动作在评审侧或用户手里。
- **opencode 评审不改代码**——返工回 qoder 分支（保持评审上下文干净，等价于「新人视角复审」）。
- 挂起票 / 决策票（无代码）不走此流程；docs 小修走 AGENTS.md 的直推惯例。
- 评审发现冻结区触碰 → 直接打回（无需商量）。
- **评审形态**：opencode 与 qoder 共用同一个 GitHub 账号，GitHub 不允许在自己的 PR 上正式 request changes，所以步 5 的评审以**评论型**提交——评审 body 就是结论载体（含「返工要求：仅 Fx」这类明确裁定），PR 的「评审通过」不靠 GitHub review 状态，靠评审 body + CI + 会话分工追踪。

## 备注

- 该分工自 2026-09-26 起为现役约定（#58 的单工具交付是此前形态，非本工作流的样板）。
- 步 3.5（`neat-freak` 文档收尾）同日为 #56 补入：评审发现的失同步（README 仍画已删节点、`.env.example` 仍承诺已失去执行点的超时）若不在这一步清掉，就会带着过期文档进 PR。
- 两边共享同一技能组（`~/.agents/skills` 的 mattpocock 工程技能：implement / code-review / tdd 等）。
