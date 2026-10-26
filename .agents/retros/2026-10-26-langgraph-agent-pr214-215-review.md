# Retro 记录：langgraph-agent PR #214/#215 评审与合并（2026-10-26）

> 会话范围：本仓库，opencode 评审侧——交付图（D1 trace 竞态 #213→PR #214；D2–D7 #212→PR #215）
> 的双轴独立评审 → 两条评审评论 → 两 PR 合并（`ccd2c79` / `93db632`）→ #213 自动关 →
> #212 收口评论 → 合并后 master 复跑门禁。
> 本文件只记本会话**新候选**（编号接续 C21 起）与观察项；既有候选复现：
> **C16 → 2/2、C10 → 2/2**（已回写原条目，均提请用户决断）。**观察日志，不是决断。**

## 新候选（按严重度）

### C21. 仓库取证命令文档全停在 pwsh，实际执行 shell 是 git-bash · Tool economy · 低-中

- **事实**：本会话全部命令经 harness 的 bash（git-bash on Windows）执行；而
  `docs/agents/issue-tracker.md` 的 Windows 注意事项与 C8/C15 的修法方向（`--body-file`、
  `gh api --input`、jq 避 `;`）都按 pwsh 5.1 写。bash 侧踩到一个同族新坑：
  `git show chore/212-review-followups:.github/workflows/ci.yml` 的 `rev:path` 冒号被
  MSYS 参数转换吃掉（报错信息里分支与路径之间的 `:` 变成 `;`），一条命令作废重写。
  反向差异（bash 里 `&&` 可作分隔符、pwsh 不能）本次无痛。
- **影响**：单次成本一小跳；但两类 shell 的坑各写一半，下一个人用哪边都只防到一半。
- **修法方向**（贴模式，不写工具）：C8 那份「本机取证贴命令」清单加一节
  **bash-on-Windows**：`git show <rev>:<path>` 改用 `git show <rev> -- <path>` 或
  `git cat-file blob <rev>:<path>`（或 `MSYS2_ARG_CONV_EXCL='*'`）；pwsh 专属条目保留
  并标注适用 shell，不删。

### C22. 批量连合 PR 时，中间 merge 的 CI run 被 concurrency 取消，master 历史留 cancelled · Automated checks · 低

- **事实**：#214 合并（`ccd2c79`）后 11 秒合 #215，ci.yml 自带
  `concurrency: group: ci-${{ github.ref }}, cancel-in-progress: true` 取消了在途运行；
  `gh run list` 于是显示一个 cancelled。本次靠「查 concurrency 配置 + 确认 tip `93db632`
  三 job 全绿 + PR 分支检查全绿」排除虚惊，并把解释补进 #214 评论留痕。
- **影响**：虚惊一次不要紧，但每合一批都要重新查一遍；且「护 master 全绿」的目视核验
  会被 cancelled 干扰——与 C13「静默不触发」是同一目视面的两种噪音。
- **修法方向**（贴模式）：workflow 或 tracker 的合并惯例加一句——**连合多张 PR 时，
  要么等上一张的 master CI 出终态再合下一张；要么合完在被合 PR 的评论留一句「中间的
  cancelled 是 concurrency 取消、非失败，绿以 tip 与 PR 分支检查为准」**。

## 观察项（本会话记录，不建议动）

- **C17 规避形态生效**：两轴都显式派 `code-reviewer`（只读），未混用 `general`。附带一个
  工具小坑：延续子代理会话时漏传 `agent` 字段被工具拒（该工具每次调用都要求 `agent`），
  秒接，无候选。
- **C11 规避形态生效**：全程 `gh run list/view --json` 取终态，未用 `gh run watch`。
- **C13 未命中**：两个 merge commit 都有 push run 且 tip 全绿，未见「静默不触发」。
- **C18 未命中**：评审后零代码改动（只有评论/合并/勘误），无「返工 delta 未复审」窗口。
- 全量 pytest 跑了 2 次分支尖端 + 1 次 Windows 敏感面 + 1 次 master 合并后定向（总机时
  约 9 分钟）——独立评审侧的必要成本，与 10-17 记录的「成本可接受」判断一致。
- 合并后在 master 复跑门禁（60 passed + ruff/mypy 0）防「合并内容 ≠ 评审内容」；本次两
  分支无冲突、内容即并集，该步属廉价保险，建议保留为批量合并后的默认动作。

## 复现记账

- **C16**（评审子代理拿不到 diff，skill 递「diff 命令」而 reviewer 只读不能跑）→
  **2/2**：本会话标准轴开局退回「在拿到 diff 之前我不会开工」，需求轴首轮交「PR A
  未评审（内容不可达）」；把两个 diff 物化到 `%TEMP%\opencode\pr{A,B}.diff` 续派才拿到
  完整报告。修法方向（编排者先物化 diff 递路径）自 10-17 记录后**未实施**，本次即复发。
- **C10**（转写子代理结论未二次核）→ **2/2**：转写标准轴报告进 #214 评论时错派一条
  发现并误计 LOW 条数，事后完整性核对才发现，补勘误评论（issuecomment-6409749315）。
  修法方向已 sharpen：转写时逐条核对发现所指文件落在该 PR 的 diff 里，数字随条目数。

## 实施记录（2026-10-26，用户拍板后）

- **C16 双侧落地**：`~/.config/opencode/agents/code-reviewer.md` 删「read the changed
  files directly」后路 + 新增 `## Diff Contract`（缺 diff 文件路径即报缺并停）；
  `~/.agents/skills/code-review/SKILL.md` 第 1 步改为编排者物化 diff 到文件（含脏树
  追加与批量场景一 diff 一文件）、第 4 步递路径而非命令。
- **C10 双侧落地**：agent 新增 `## Attribution and Counting` + Output Format 多 diff
  分节；skill 第 5 步要求多 diff 分节展示，且转写进 PR body/评论前逐条核对文件归属
  与计数。
- **C17 同批落地**：skill 第 4 步明写两轴都派只读 `code-reviewer`；agent description
  改中文并补双轴与 spec 符合度。
- C21 / C22 未动，仍待决。
