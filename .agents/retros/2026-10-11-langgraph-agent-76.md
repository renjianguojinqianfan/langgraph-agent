# Retro 记录：langgraph-agent #76（2026-10-11）

> 会话范围：本仓库，#76（子任务产物双登记 + 迟到 hand-back）实施至 PR #120 合并。
> 本文件是跨会话复盘候选的**观察日志**，不是决断——按用户裁定（2026-10-11）：
> 「多观察几个会话的复盘再决定」。每条候选带复现计数，同一候选在 ≥2 个独立会话
> 复现即提请实施；用户也可随时提前拍。账本位置：`<repo>/.agents/retros/`（与
> `.agents/skills` 同为仓库跟踪内容）。

## 候选（按严重度）

### C1. PR 面无「票号 + Closes」机械门 · Automated checks · 复现 2/2 · **已决：不建（用户 2026-10-12 拍板）**

- **事实**：`docs/agents/workflow.md` 步 4 把「标题带票号（Closes #N）」写成硬惯例；
  全仓唯一机械校验 `scripts/validate_commit_msg.py` 只管 commit 消息且只经本地钩子
  （CI 从不跑 git 钩子，#115 body 自述）。PR 标题/body 零校验。本会话实测：
  PR #120 标题只有 `#76`、body 无 `Closes`，合并前人工补回；漏补后果 = 票不自动关、
  `blocked_by` 依赖边不释放（本会话开局正卡在该边上）。
- **复现 2（#121 会话，2026-10-11）**：PR #121 标题带 `#46`、正文用 `Refs` 不用
  `Closes`——且这是**正确**的（#46 票面「两步、各自一张 PR」，第二步 grader 未做，
  关票会灭掉第二步 AC）。修法方向据此修正：机械门不能只认 `Closes|Fixes|Resolves`
  三件套，必须放行「显式 `Refs #N` + 一句理由」的形态，否则门禁会逼出误关。
  证据：PR #121 范围段 + 评审结论评论（issuecomment-6110071986）。
- **决断记录**：用户 2026-10-12 拍板**不建**该机械门。在账理由（拍板后补的旁证，
  非拍板依据）：C1 两起复现之后的本波四张 PR（#121/#123/#112/#107）标题票号与收口
  关键字全部正确（含 #121 正当使用 `Refs`、#112/#107 用 `Closes`），现有约定+评审
  人肉核未再漏——门禁的边际价值不成立，维持现状。
- **修法方向**（建检查，不写规则）：`validate_commit_msg.py` 的 `evaluate()` 提为可
  复用函数 + `pull_request` CI job——非 docs 类 PR 必须含 `#\d+`，且收口关键字三选
  一：`Closes|Fixes|Resolves`，或显式 `Refs #N` + 理由（两步 scope 票的正当形态，
  见复现 2）；paths 全落 `docs/` 只提醒（对齐 commit-msg 的 docs 豁免）。
  形态仿 protected-files guard job。

### C2. 决策票终裁不回写票面 · Navigation · 复现 1/2

- **事实**：#76 的 A+C 终裁在 2026-09-30 评论里，票面 body 仍是「候选修法（未拍板）」
  旧文——本会话读 6 条评论才拼出真相，开局最大的一笔探索成本。对照 #46 body 有
  「现状（以本节为准）」段，开袋即食。同类漂移本会话还抓到两处 ADR（自评审 F1）。
- **修法方向**（判断题，落文档）：`docs/agents/issue-tracker.md` 加流程规则——拍板评论
  落地后把终裁回写进票面 body 置顶「现状」段，评论留史，下一会话只读 body。#76 补一条
  回写评论即可闭环。

### C3. frontier 查询没有可直跑命令 · Tool economy / Navigation · 复现 1/2

- **事实**：`docs/agents/issue-tracker.md:79-80` 用文字写 frontier 纪律（`blocked_by=0`
  且无 assignee），本会话用 6+ 次 `gh issue view` 拼齐各票状态；`gh issue view N
  --comments` 长串截断，`Select-Object -First/-Skip` 拼接易错。
- **修法方向**：tracker 文档该段旁贴 copy-paste 命令（`gh api` + jq 一遍输出
  number/state/blocked_by/assignees/labels；`--json body` 绕开截断）。贴命令优于写
  脚本——文档即权威，零维护代码。

## 观察项（本会话记录，不建议动）

- ADR/README 行为断言与代码漂移：无便宜机械门；`docs/agents/workflow.md` 步 3.5
  neat-freak 要求已覆盖「语义变必须同步」，缺执行不缺规则。
- 全局 AGENTS.md：短而稳，无 no-op、无搬迁候选。
- agent 自身 edit 工具参数笔误（`newString`/`path` 丢参 ×2）：工具报错秒接，无环境侧修法。

## 复现记账规则

- 后续每场 retro 若命中同条候选，在本文件对应条目把「复现 n/2」加一，并附一句证据。
- 到 2/2 提请用户决断是否实施；用户提前拍板则随时实施并在条目下标「已决：<结论>」。
- 新候选另起一节（C4、C5…），严重度重排全表。
