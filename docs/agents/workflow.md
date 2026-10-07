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
| 4 | **qoder** | 提交 PR：分阶段 commit **不 squash**；标题带票号（`Closes #N`）；body 调 `pr` skill 写（Summary 最小可视化 / Before-After Evidence / Merge Danger）；验证状态四态如实写（通过/跳过/未运行/产物验收） |
| 5 | **opencode** | 以评审者身份独立评 PR：调 `code-review` skill 双轴评审 + 门禁核对（ruff/mypy/pytest/`--check`）+ 冻结区检查 + live 视票面需要（细则见「评审动作清单」）；**只提意见与返工要求，不直接替改** |
| 6 | 评审侧/用户 | 合并：调 `verification-loop` skill 走收尾门（机械验证 + 端到端验收）→ CI 全绿 + opencode 评审通过 → merge commit（不 squash） |

## 边界

- **实施者不合并自己的 PR**——合并动作在评审侧或用户手里。
- **opencode 评审不改代码**——返工回 qoder 分支（保持评审上下文干净，等价于「新人视角复审」）。
- 挂起票 / 决策票（无代码）不走此流程；docs 小修走 AGENTS.md 的直推惯例。
- 评审发现冻结区触碰 → 直接打回（无需商量）。
- **评审形态**：opencode 与 qoder 共用同一个 GitHub 账号，GitHub 不允许在自己的 PR 上正式 request changes，所以步 5 的评审以**评论型**提交——评审 body 就是结论载体（含「返工要求：仅 Fx」这类明确裁定），PR 的「评审通过」不靠 GitHub review 状态，靠评审 body + CI + 会话分工追踪。

## 评审动作清单（步 5 细则）

> 2026-09-26 由 PR #69 评审→返工→复核→合并的首个完整循环补入：以下路由此前只活在临时交接文档（handoff）里，现在落进本体——handoff 是会话态，规则必须有持久家。

- **双轴评审**：调 `code-review` skill（规范轴 + 需求轴**并行子代理**）。规范源 = `AGENTS.md` + `pyproject.toml` 注释 + 全局 `~/.agents/AGENTS.md` §7；需求源 = 票面 AC 逐条对照。规范轴另必带 **Fowler 坏味道基线**（`code-review` 技能内置 13 条）：repo 文档规范优先于基线；ruff/mypy 已机械覆盖的跳过；基线命中**一律是判断题**——不当硬违规打回，也不当 scope creep 放过。
- **安全评审**：涉及**闸门 / 工具面 / 子代理权限**的 PR 必用 `security-review` skill；纯观测面（#58 件清单这类）可跳过。
- **件清单裁判**：能力面 / 工具面 PR 要跑一个 mock 任务（headless 管线 + 自定义 MockLLM 脚本）后核对 run manifest 的 capability / `subtask_face` 行与票面意图一致——「这次子代理能干什么」必须可 diff、可回看。
- **live 视票面**：口径唯一权威 `scripts/LIVE_E2E.md`（含「先 A/B 复跑再归因」）。
- **评审交付物**：评审 body（结论 + 四态 + 逐条裁定）+ 必要时开跟进票、补标签（按 `docs/agents/triage-labels.md` 三类分诊）。

## 返工回环（步 5 → 6 之间）

- 评审提出返工 → qoder 在**同一分支**修 → push → CI 复绿。
- 复核口径分两档：**纯注释 / docs 返工** → 评审读返工 diff + 新 head CI 全绿 + `mergeable` 即可收口合并；**含代码的返工** → 回到步 5 完整口径（受影响面的门禁 + 相关测试至少重跑一轮）。
- **复盘**：合并后（或返工超一轮时）调 `retro` skill 会话复盘——机械错误变确定性检查、判断变编码规范，落点回写 `AGENTS.md` / `pyproject.toml` / 门禁（对应全局 AGENTS.md §5 反熵回路；上游主流程把 retro 放在 code-review 之后）。`diagnosing-bugs` 修完硬 bug 同样跑一次，问「什么检查本可以防住它」。

## 并行实施（多 worktree，2026-09-28 #49 试水首证）

单票仍走上面七步；多票同时开工时叠加以下约定：

- **一票一 worktree**：`qoder --worktree <票号-短名>`（落 `.qoder/worktrees/`，已 gitignore）。AGENTS.md 在子路径自动注入，规则层不用向子代理复述
- **claim 走 GitHub**：开工先 `gh issue edit <N> --add-assignee @me` 防并发会话撞车；子代理共享的父任务清单只认含本票号的条目
- **开票前先现查一次 open 清单**：claim 只防「两张会话实施同一张既有票」，**不防「同时新造一张同内容票」**——opencode 与 qoder 共用同一个 GitHub 账号，连作者不同这个信号都没有。实证：2026-09-30 同一轮里 qoder 开 #101、评审侧早 5 分钟为同一 bug 开了 #100。撞了就把自己那张独有的东西搬进对方票的评论，再 `close` + 打 `duplicate`，并在关闭评论里写清**对方为什么赢**（本次：对方根因钉到 `rec["status"]` 原地变更，所以 AC 全机械可裁，我判的 `needs-info` 是没核到那一步）
- **并行段只到步 3**：步 3.5 文档收尾、开 PR、评审、合并一律**串行**（由主会话逐张做）。撞车面从来不在源文件，在权威文档面
- **ADR 发号由合并门统一**：两个 agent 各建 `docs/adr/0005-<不同slug>.md` 时路径不同 → git 不报冲突、rebase 也不报，合出来就是两个 0005，而 AGENTS.md/README 里所有 `docs/adr/0005` 指向同时失效。这条对机械门禁**隐形**，只能靠发号顺序
- **master 在并行批中间前进会造「幽灵 hunk」**：各票分支都起于当时的 master，此后任何先落地的小改（docs-only 直推、先合的 PR）都会让**两点** diff（`git diff master <head>`）比出一条「本分支回退了那行」的假改动——分支是**没带**那个 commit，不是改了它。实证：#96 被要求「拿掉 p1.md 的 hunk」，而 `/pulls/96/files` 只有 2 个文件、`mergeable_state=clean`。**diff 一律走三点口径**（`git diff master...<head>` / `gh pr diff <n> --name-only`），报返工前先核 `gh api repos/<owner>/<repo>/pulls/<n>/files`
- **工具链共享**：worktree 是干净检出、没有 `.venv311`——门禁一律用绝对路径 `E:\code\demo\langgraph-agent\.venv311\Scripts\python.exe`；前端可 junction 主检出 `node_modules` 免 `npm ci`（CI 仍以 `npm ci` 口径终裁）
- **brief 纪律**：委托 prompt 要短，且显式授权「依据缺失就停下问，不许猜」——试水中两次源头截断都靠这条救回
- **派活前按 AC 跨层数估工作量**：一条 AC 动一层，五条 AC 动五层（判定→协议→配置→事件→测试）≈ 顶到一轮子代理预算上限——#57 就这么中断的（AC1-4 完成、AC5-7 半路）。超三层先拆再派，或按语义边界直接拆两张 PR
- **子代理中断的 WIP 靠 scratch 分支续，不靠 patch**：撞轮次上限时树里往往是几个文件的**未提交**改动。正确起手是 `git switch -c wip/<slug>`（脏改动跟着过去）→ `git add -A` → 单 commit，再切回特性分支做干净态评审；续做时**从新 master 切分支只 `git cherry-pick` 那个单 commit**。别只导 `.patch`——未跟踪文件容易漏、patch 不带身份也不能直接跑门禁；patch 至多当备份。⚠️ 若特性分支后来 rebase 过，scratch 分支的父提交是**旧链**，直接 checkout 续做会把已被替换的提交带回来，必须先 cherry-pick 重接
- **边界靠机械断言，不靠任务卡**：试水实证卡守不住文件面（禁了文档，agent 第二轮还是写了）；有效闸门是 push 前四断言——`git status` 空 / worktree 只有主树+本票 / 冻结区 diff 空 / 远端无此分支
- **分离评审不是仪式**：agent 自评全绿之后，步 5 独立评审仍抓到了人漏读的真 bug（无可再挤时仍自称压缩）——不因自评绿而免
- **多票大工程可整 spec 路由 `implement-spec`**：整份 spec 单集成分支落地、ticket 当任务图、实施子代理各占 worktree/分支、frontier 并发、最后跑一次 `code-review`——本节手搓约定（claim 走 GitHub、ADR 发号统一、四断言、brief 纪律）继续有效，只是执行形态换成上游正式版

## 备注

- 该分工自 2026-09-26 起为现役约定（#58 的单工具交付是此前形态，非本工作流的样板）。
- 步 3.5（`neat-freak` 文档收尾）同日为 #56 补入：评审发现的失同步（README 仍画已删节点、`.env.example` 仍承诺已失去执行点的超时）若不在这一步清掉，就会带着过期文档进 PR。
- 两边共享同一技能组（`~/.agents/skills` 的 mattpocock 工程技能：implement / code-review / tdd 等）。
