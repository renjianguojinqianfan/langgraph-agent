# Retro 记录：langgraph-agent S5 实施与 PR #150 评审（2026-10-17）

> 会话范围：本仓库，一次长会话——S5（#139）落地 → 真模型代跑 → 提交/推送 → PR #150 → 两轮评审与返工。
> 本文件只记本会话**新候选**（编号接续 C15 起）；既有候选的复现计数见文末。
> **观察日志，不是决断——下列任何一条都未实施，等用户决断。**

## 候选（按严重度）

### C16. 评审子代理拿不到 diff：skill 递的是「diff 命令」，reviewer 却不能跑命令 · Information access · 高

- **事实**：`~/.agents/skills/code-review/SKILL.md` 第 1 步让编排者「Capture the diff command」，
  第 4 步给子代理的简报写「The full diff command and commit list」；而本机 reviewer 的定义
  （`~/.config/opencode/agents/code-reviewer.md:25-28`）明确 **strictly read-only**：「You cannot run
  shell commands … If you need a diff, either the caller supplies it or you read the changed files
  directly. Do not attempt `git diff`」。两者直接冲突。
- **本次后果**：两轮评审（首轮单 reviewer、次轮双轴）都只能读工作树，无法区分「本 PR 改的」与
  「之前就腐的」。标准轴因此把 `README.md` 的旧拓扑/闸门段落报成硬违规，并自己加了「可能早于
  本片就腐」的免责句——**问题不在它报错，而在评审物不对**：它没有 diff 可看。我当时已经知道
  reviewer 不能跑 git（甚至把「You cannot run git」写进了 prompt），却仍选择「读工作树」，
  没有把 diff 落成文件递过去。
- **修法方向**（skill 文本，全局）：第 4 步改成——**编排者先物化 diff**：
  `git diff <base>...HEAD > <tmp>/review.diff`，工作树脏时再加 `git diff <base>`（含未提交改动）
  与 `git log <base>..HEAD --oneline`，把**文件路径**连同「你是只读的」一起交给两个子代理。
  不再把命令本身当提交物。

### C17. 双轴评审没指定子代理类型，混用了可写的 `general` · Tool economy / 评审纪律 · 中高

- **事实**：`code-review/SKILL.md:11` 只说「Both axes run as **parallel sub-agents**」，第 4 步
  也只写「sub-agent」，**没有指定 agent 类型**。本次我派了：标准轴 → `code-reviewer`；
  需求轴 → `general`。当时的判断链是：catalog 里 `code-reviewer` 的描述只有
  「correctness, regressions, security, maintainability」，没有「spec 符合度」；`general` 的描述是
  「researching complex questions and executing multi-step tasks」，于是把「对着 issue AC 逐条比对」
  归类成了 research。
- **为什么不对**：两条轴都是**评审**，不是研究；`general` 是通用执行型子代理，**没有 reviewer
  那套只读权限**（不能改文件、不能跑命令、不能 web）。本次它没有改东西，但「实现/评审分离」
  因此在需求轴上不是结构性的，只是我口头约束的。另外两条轴的产出**来源不一致**（一个 reviewer、
  一个 general），报告的可比性与可复现性都差一截。
- **修法方向**（skill 文本，全局）：第 4 步明写「**两条轴都用 `code-reviewer`**（只读），
  需求轴把 spec 路径 / 抓来的正文一起递进去；不要用 `general` 做评审」。第 2 步已经要求
  编排者自己抓 issue/spec（`docs/agents/issue-tracker.md`），所以不需要一个能跑命令的评审代理。
  可选：把 `code-reviewer.md` 的 catalog description 补上「spec/issue conformance」，让映射没有歧义。

### C18. 评审后的返工没有再评审就差点推送 · 评审纪律 · 中

- **事实**：首轮评审报 10 条 → 我逐条修 + 补测试 → 直接 `git commit`；随后在写 PR 的 Merge Danger
  时又发现 checkpoint 格式问题，追加了 v1/v2 判定 + 测试。**这整段增量没经过任何评审**，我准备
  直接 push，是用户问「等一下，你review了吗」才拦下。次轮双轴又报了 9 条（其中 `replan` 永久改
  模式、用户可见错误仍叫 `Planner/Executor`、docstring 虚构 `older` 之外的枚举、sentinel 夹具漏
  新字段等）。不主张这 9 条全是返工引入的；问题在**返工后的 delta 无人复核**，而首轮结论被当成
  了对最终代码的结论。
- **修法方向**（skill 文本，全局）：`code-review` 增加第 6 步——**评审是会被后续改动作废的**：
  任何 post-review 改动（尤其是「因为评审而改」的）都要对 delta 重跑两轴（`git diff <last-reviewed>`
  或上次评审的 commit），通过后才能说「评审过了 / 可以推」；PR body 里的数字与事实同属 delta。

### C19. 改图拓扑时没有「checkpoint 格式」的导航指针 · Navigation · 中

- **事实**：S5 把 `planner`/`executor` 合成 `agent`，图拓扑变了；旧 checkpoint 快照仍打 v1 戳。
  这个部署风险**直到写 PR 的 Merge Danger 才被想到**，随后才去加 `CHECKPOINT_FORMAT_VERSION 1→2`
  与 `older` 拒绝分支。`docs/pivot-plan-build.md` §S5 行、ADR 0008 都没提存储格式；
  `docs/migration-langgraph-1x.md` §2 才是戳记的权威，但没有从 S5 材料指过去。
- **影响**：若照原样发布，旧快照会在新图上「未证明地续跑」——正是仓库格式判据要拒绝的那类。
  属于「昂贵的发现得晚」，不是代码 bug。
- **修法方向**（导航指针，仓库文档）：在 `docs/pivot-plan-build.md` 的切片纪律或 ADR 0008 的
  「代价」里加一行——**凡改图拓扑，先读 `docs/migration-langgraph-1x.md` §2，决定是否 bump
  `CHECKPOINT_FORMAT_VERSION`**；不新建文档。

### C20. `_full_state()` 自称「每个字段都填了」，实际是手写清单 · Automated checks · 中低

- **事实**：`backend/tests/test_checkpointer.py:59-82` 的夹具 docstring 写「An AgentState with every
  field populated」，但字段是手写 dict，round-trip 的 sentinel 只遍历 `state.items()`。S5 新增的
  `mode` / `round_steps` 因此**静默落在 tripwire 之外**，次轮评审才发现。测试声称的保证对新增
  字段是假的。
- **修法方向**（机械检查，仓库）：夹具的键从 `AgentState.__annotations__` 派生（或断言
  `set(fixture) == set(AgentState.__annotations__)`），新字段必须先补值才能通过；这样「新增状态
  字段」自动变成一次红灯，而不是靠评审员记得。

## 观察项（本会话记录，不建议动）

- **机械替换的残渣被评审员接住了**：`COMPLETED` → `INTERRUPTED` 的批量替换在测试里留下重复的
  `("INTERRUPTED", "FAILED", "INTERRUPTED")` 元组与一个陈旧的 smoke 检查键名。评审标准轴报了，
  属评审设计生效；不另立候选，但它是 C18「delta 必须复核」的一个实例。
- **全量 pytest 跑了约 6 次**（每次 65–85s）＋ mypy；中途已用定向文件跑，收尾才全量，成本可接受。
  暂不立 Tool economy 候选。
- **网页端亲手点一遍仍缺**：agent 没有浏览器通道，ADR 0008 又明确把真模型验收定为人工。与既有
  C6（UI 验收没有 agent 可用的信息通道）同源，**仅相邻实例、不作正式加成**。若要做，最便宜的
  是 mock-LLM 的 Playwright 冒烟（不碰真模型计分面），但与转向后「不搞无人值守评测」的立场有张力，
  留给用户决断。
- **双轴产出本身有效**：需求轴抓住 `replan` 语义与「反问被当计划」，标准轴抓住 sentinel/docstring
  漂移；两轴分开报确实避免了互相掩盖。问题在**派谁去**（C17）与**给什么**（C16），不在双轴本身。

## 复现记账

- **C10**（从评审报告转写 PR body 时未机械核对每个数字/事实）：本会话 PR body 的 `682 passed` /
  18 事件 / CI 3/3 均在写作前实跑核对，**未命中，不作正式加成**。
- **C11**（`gh run watch` 收尾被截断）：本会话用 `gh pr checks --watch` 拿到完整终态，**规避形态，
  不作加成**。
- **C6**：仅网页点击这一条相邻实例，见观察项，**不作正式加成**。
- 其余既有候选本会话未命中；新候选从 C21 起继续编号，严重度重排全表。
