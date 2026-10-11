# Retro 记录：langgraph-agent #121（2026-10-11）

> 会话范围：本仓库，#46 第一步（expectation + UNVERIFIED 终态 + 悬空清扫）从独立评审
> （opencode 侧）到 PR #121 合并、#48 关闭的全程。
> 账本惯例与记账规则见同目录 `2026-10-11-langgraph-agent-76.md`；本文件只记本会话
> 新候选（编号接续 C4 起）与对既有候选的复现追加。**观察日志，不是决断。**
> 本会话命中既有候选 C1（已在该文件加复现 2/2 并修正其修法方向）。

## 候选（按严重度）

### C4. `_needs_confirm` 重算块无机械守卫 · Automated checks

- **事实**：AGENTS.md 跨任务安全边界表自述该块「**无机械守卫**（guard 不覆盖它），
  改动前问人」——同表的熔断冻结区有 guard-protected-files job 机械拦截，这块 P0
  死循环根因（graph.py `_after_tool` 反复路由回 `human_confirm` → GraphRecursion-
  Error）没有。本会话双边人肉核了同一件事：实施侧在 Merge Danger 自证、评审侧
  `git diff master...HEAD | grep _needs_confirm` 确认只以 docstring 一行上下文出现。
  块有天然定位标记：`# NOTE (red line)` 起、到 `return state`（`def reflect` 前）。
- **修法方向**（建检查）：guard job 加一条块级断言——按上述标记从 base 与 HEAD 两版
  `nodes.py` 提取该块正文做比对，变更即红，`[OVERRIDE]` 标题逃生舱口径不变。与现有
  resilience/registry 守卫同文件同形态，约十行 shell/python。

### C5. 在飞 PR 文件碰撞集靠人肉求交 · Automated checks

- **事实**：PR #121 Merge Danger 初版只列 3+1 处碰撞，漏了与 #107 的 `AGENTS.md` /
  `README.md`、与 #112 的 `README.md`——漏的恰是两张承重文档。评审侧逐张
  `gh pr diff <n> --name-only` 求交才发现（F1 返工项），实施侧返工前又独立复核一遍：
  同一次求交做了两遍人肉。三张在飞 PR（#107/#112/#121）两两相撞是既成事实，检查不
  阻止碰撞，只保证碰撞集被**完整声明**、后合方拿到真导航。
- **修法方向**（贴命令，不写脚本——仿 C3 结论）：tracker 或 workflow.md 提 PR 清单旁
  贴 copy-paste 命令（`gh api repos/<owner>/<repo>/pulls/<n>/files --jq '.[].path'`
  对全部 open PR 求交），提 PR 前与评审时各跑一遍。

### C6. UI 验收没有 agent 可用的信息通道 · Information access

- **事实**：本票前端改动（UNVERIFIED 徽章 orange-600、失败项文案 `failures.join`）
  的验证四态里「浏览器人肉」只能记「**跳过**」——实施与评审两侧都没有看图能力，
  机械三门（tsc / check:sse / build）之外的纯人眼风险（与 INTERRUPTED 的 amber 是否
  一眼分得清、长失败文案截断）无人看过，只能记账到发版前。
- **修法方向**：frontend 加 Playwright（或 puppeteer）截图 smoke——起 dev server、
  注入 fixture 态 task（UNVERIFIED + failures）、对 TaskHeader / HistoryPanel 截图
  落盘；把「跳过」变成「产物验收（截图）」。成本中等，收益覆盖所有前端 PR 的人眼项。

### C7. PR 开后补 commit 无点名机制 · Coding standards（判断题，落文档）

- **事实**：c90ce59（architecture.md TaskStatus 两处清单补 UNVERIFIED）在 PR #121
  开后 push，PR body 与评论均未点明分支动过；评审侧读 body 时不知道 head 已变，靠
  比对 commits 列表才发现，收口复核专门加了「head 未动」一项。neat-freak 收尾跑在
  PR 之后是同一缺口的过程表现。
- **修法方向**：workflow.md 返工回环段（或步 4）加一句——开后补的 commit 要在 PR
  评论点明「哪个 commit、补了什么、为什么现在补」。docs-only 返工收端口径已要求读
  diff，点名让 diff 可定位。纯流程规则，无机械门候选。

### C8. pwsh 下 gh 输出落盘再读出现 mojibake · Tool economy

- **事实**：`gh pr view 121 --json body | Out-File -Encoding utf8` 落盘后用 read
  工具读出 GBK 式乱码（PowerShell 5.1 的 utf8 带 BOM + 读取端编码推断不符），整篇
  body 重读一遍才继续；同会话 gh 直出 terminal 的中文渲染正常。
- **修法方向**：本机 pwsh 会话里，长 JSON / body 要么直出 terminal，要么用 write
  工具写临时文件（UTF-8 无 BOM，read 正常），别用 `Out-File -Encoding utf8` 中转。

## 观察项（本会话记录，不建议动）

- 两步 scope 票用 `Refs` 的裁定链走得干净：票面具体声明胜交接文档通用惯例，实施侧
  没有自行拍板而是提请评审侧裁——这个行为正是想要的，已回进 C1 修法方向。
- ADR 发号由合并门统一的规则本次生效（0005/0006 无重号，两个在飞 PR 都不碰
  `docs/adr/`）；同文件串行链（#100 → #76 → 本票）前提核验也到位。
- 既有测试重裁（4 条 COMPLETED → UNVERIFIED）逐条附理由、变异取证八组——实施侧
  自评审的深度足够，独立评审抓到的全是流程/文档面（碰撞声明、AC 对照），零代码缺陷。

## 复现记账规则

- 沿用 `2026-10-11-langgraph-agent-76.md` 的规则：同候选再命中往该文件对应条目加
  复现计数；到 2/2 提请用户决断；新候选继续 C9 起编号，严重度重排全表。
