# Retro 记录：langgraph-agent #254 三切片交付与双轴评审（2026-10-31）

> 会话范围：两条同日会话。
> ① **实施会话**（DSH，16:31–21:47）：认领 #254 → 3 个串行 PR（#272/#273/#274）→ 自评审返工 →
> 独立评审 → 合并后遗留整理；
> ② **评审/合并会话**（opencode，本工作区，19:39–21:08）：三 PR 双轴评审 → 预演合并 →
> 按序合并（`8f0ba0e` / `39d7f4d` / `0764a85`）→ 开 #275/#276 → 自 retro。
> 结果：3 个 PR 全部 APPROVE + CI 3/3 绿后合并；#254 关闭；遗留 #271/#275/#276 各有家。
> 本文件只记**新候选**（编号接续 C23 起）与观察项；既有候选复现见「复现记账」。
> **观察日志，不是决断。**

## 先记功（本次做对、值得保持/晋升的）

1. **red 必须证伪旧实现**：3/3 红测试都先在旧实现上真失败——slice 2 的 `/trace` 用例实测读到
   `<trace-dir>/../secret.jsonl`，slice 1 实测 `LLM_API_KEY` 进入 MCP 子进程。比「新测试绿」强
   一档的证据形态，PR body 直接引用了失败断言与越界路径。
2. **自评审 → 返工 commit 分离**：3 个 slice 各有一次「按自评审返工」独立 commit（不 amend、
   不 squash），评审发现与修复在历史里可追溯；独立评审的 3 条 LOW 也都以单独 commit 收口后才合并。
3. **预演合并 + 树等价证明 + 对照实验**（opencode 会话）：实合前在临时 worktree 按序真合，
   冲突/解谱/门禁前置暴露；合并后 `git diff <预演树> master` 为空，证明「实合结果 = 已验证树」；
   16 个测试失败用主 checkout 对照实验定性为环境而非合并破坏。
4. **双轴评审有计划外收获**：独立评审提出 npmrc 凭证注释与 `TokenIssuer` fail-closed 两个纵深
   问题，都转化为跟进票（#275/#276），没有拖回已 APPROVE 的 PR。

## 新候选（按严重度）

### C23. 冒烟清单「双址」无机械守卫（AGENTS.md ↔ ci.yml）· Automated checks · P0

- **事实**：AGENTS.md 明写「两边保持同一清单」，本次三个兄弟 PR 各往这两处同点追加
  `test_trace.py` / `test_auth.py`；#273/#274 的合并冲突正落在这两行，解冲突靠逐行人肉核对；
  opencode 规范轴子代理也独立点了该隐患。全仓清点：无任何测试/脚本解析这两个文件
  （`test_static_guard.py` 守 StaticFiles 路径契约，`test_gitignore_visibility.py` 用临时 fixture）。
- **影响**：冲突修复丢一行 = Windows 冒烟面静默缩水，CI 仍绿（Linux 全量覆盖多数逻辑，
  Windows-only 分支可能漏跑）。
- **修法**：新增 `backend/tests/test_smoke_list_parity.py`：解析 AGENTS.md 那条 Windows 冒烟
  命令里的 `backend/tests/*.py` token 与 `ci.yml` 的 `backend-windows-smoke` job 参数，断言两集合
  相等且文件存在；毫秒级、平台无关。已实测当前 11 个文件两侧解析结果一致（可直接作绿基线）。
- **定级理由**：规则已文档化 + 零守卫 + 本次实际发生且机械可判。

### C24. DSH/沙箱验证矩阵与临时树跑门禁配方未文档化 · Information access · P1

- **事实**：同一批验证在两条会话各重新诊断一次。① 实施会话 5 次全量 pytest 全部需要一次性
  full-access（沙箱内 tmp/子进程限制），git push 同样需要；② opencode 会话在临时 worktree 连撞
  四墙（pre-commit 相对 `.venv311` 找不到、ruff/mypy 拒绝在沙箱临时路径建 cache、pytest tempfile
  探测失败、worktree 须放普通盘符路径），16 个失败靠对照实验才定性为 msys 子进程限制
  （`NtCreateDirectoryObject: 0xC0000022`）。这些知识只活在 handoff 会话态与评论里，没有仓库文档。
- **影响**：每碰一次是 10 分钟量级 + 多次批准；最危险的是把环境失败误读成「合并破坏了代码」。
- **修法**：新建 `docs/agents/verification-notes.md`（若判定属机器局部环境，则放用户级
  `~/.agents/` 并在 AGENTS.md 留指针）：
  1. 沙箱矩阵：受限模式下必败的测试类（msys/Git-bash 子进程、tmp 目录探测等）、
     对照实验定性协议、四态记录要求；
  2. 临时树/预演合并跑门禁配方：正常盘符路径、钩子 `--no-verify` 记理由、ruff `--no-cache`、
     mypy cache 指主检出、脚本写文件不内联；
  3. full-access 批处理约定：一次批准内把「targeted → 3 连跑 → 全量」串成一条命令。
- AGENTS.md「验证入口」加一行前置触发词指针（`沙箱 / 预演 / 临时树 → docs/agents/verification-notes.md`），
  不内联（渐进披露）。

### C25. 同票多切片的合并与工作区纪律没有家 · Coding standards（判断 → 落 workflow）· P1

- **事实**：「顺序合、同点追加两条都留、后合者 rebase 解机械冲突」只写在 #254 票面评论；
  预演合并、树等价证明、合并后复跑也没有流程条目。实施侧同票三切片共用一个 worktree 串行切入
  切出（3 分支 + 2 次 stash pop），编辑工具多次报「file changed since it was read」；评审侧两个
  子代理在 `fix/254-auth-guard` checkout 上找 slice 1/2 的代码，靠 diff 纠正。
- **修法**：`docs/agents/workflow.md` 增「同票多切片」小节：一 slice 一分支（能开 worktree 就开）；
  预演合并 + `git diff <预演树> master` 空 diff 收口；同点追加类冲突解谱 = 都留 + 逐行核对清单；
  合并后 master 复跑门禁；合并后清理已合并分支（本地默认，远端删除需确认）。C23 上线后清单类
  冲突机械可判，人只剩 `security.md` §8 一种。

### C26. code-review 子代理供给不足：交付记录、工作区边界、实测门槛 · Skill · P1

- **事实**（opencode 会话）：① 规范轴把「3 连跑证据缺失」判 HARD——证据在票面/PR 交付评论里，
  但子代理只拿到 diff + 规范源；② 需求轴 M1（单反斜杠 `\server\share` 拨 SMB）是错的——
  读码断言 pathlib 语义，编排者实测 12 种拼写后证伪；③ 两个轴都去读工作区（当时 checkout 在
  slice 3），发现 slice 1/2 内容不在树里后靠 diff 纠正。DSH 侧通过「`git show <sha>:<path>` +
  物化 diff」规避了 ③，但 ①② 同源风险仍在。
- **修法**（用户级 `~/.agents/skills/code-review`，需拍板）：step 2/4 明确两个轴都附
  **issue 正文 + 交付评论（含验证记录）**；step 4 明示「评审输入 = 物化 diff + 按提交对象读文件，
  工作区可能停在别的分支，不得以工作区为源」；step 5 加「OS/库语义类发现必须先最小脚本实测
  才可进结论」。

### C27. PR body 在返工后失同步 · Coding standards（落 workflow / PR 模板）· P2

- **事实**：#274 的独立评审 LOW-1：返工 commit（改名、撤销 `TokenIssuer` 回退、证据数字
  27→32 / 734→739）后 PR body 仍是 commit 1 快照；评审者为这三个数字往返一条评论。合并后
  用户拍板把「PR body 是合并裁决载体」写进评审意见。
- **修法**：workflow 步 4 加一句「返工 commit 后刷新 PR body：Summary 的符号名、Evidence 的
  命令与数字与最终 head 对账」；或 PR skill 模板加一个「最终 head 对账」清单项。

### C28. 同一批 PR 的双评审方没有分工 · 流程 · P2

- **事实**：同一天两条会话对同一批三个 PR 各跑了一轮双轴独立评审（DSH 侧 reviewer teammate +
  opencode 双轴），各自固定点不同（DSH 评到最后 head，opencode 评到中途 head 并最终合并）；
  两份 APPROVE 结论并存，opencode 三会话合计约 350k tokens。两边都「独立」= 没有一方对
  merge gate 唯一负责。
- **修法**：workflow 步 5 明确「同一批次只设一个 merge-gate 评审方（默认 opencode）；
  实施会话内的双轴评审只作为步 3 自评审，不作为合并依据」；若确实要双评审，评论里必须写
  固定 head SHA，后改 head 需复审。

### C29. 契约变更时既有测试的调整口径未定义 · Coding standards · P2

- **事实**：AGENTS.md 红线「既有测试只增不减、不得跳过或放宽」；#276（`TokenIssuer`
  fail-closed）必然要调整 `test_auth.py` / `test_qa_p1_auth.py` 里直接构造空/占位密钥的用例。
  现有文档没有「有意修改既有断言」的判定口径，执行者要么不敢改、要么悄悄放宽。
- **修法**：workflow「返工回环 / 边界」加一行：契约性变更允许改既有断言，但 PR 必须逐条列出
  「旧断言 → 新断言 → 为什么是契约变化而非放宽」，并由评审侧对照红线核账。

### C30. Windows 平台操作小抄仍缺（gh `@me`、fork PR CI 审批、git-bash `/tmp`）· Tool economy · P3

- **事实**：① 本会话 `gh issue edit 254 --add-assignee @me` 被 PowerShell 当成 splat，
  报 `flag needs an argument`，加引号 `'@me'` 即解；② fork PR #277 的 workflow run 结论是
  `action_required`，需维护者审批后才跑 CI（首见）；③ opencode 会话读 `%TEMP%` 文件时把
  git-bash 的 `/tmp` 映射算错，白掉一次读（C21 同源）。
- **修法**：`docs/agents/issue-tracker.md` 的 Windows/PowerShell notes 补三行：`@me` 加引号、
  fork PR 首次 CI 需 `action_required` 审批、git-bash `/tmp` 与 Windows `%TEMP%` 的映射与取值方式。

### C31. AGENTS.md skills 段的 provenance 载荷可压缩 · No-op（判断）· P3

- **事实**（opencode 会话提出）：`## Agent skills` 的「由 setup-matt-pocock-skills 技能生成…」
  一段对不维护该段的 agent 不改变行为，约占数行固定载荷。
- **判断**：该段是仓库对 mattpocock 技能组的约定，压缩前需确认技能组仍读它；属判断题，
  优先级低于 C23–C30。
- **修法（若做）**：压成半句 + 保留四个 `###` 入口标题。

## 复现记账

- **C16**（评审子代理拿不到 diff）→ **规避生效**：opencode 父会话先把 3 个 diff + spec + standards
  物化到 `%TEMP%` 再派轴；DSH 侧自评审按 SHA 指提交对象。C16 的修法已落地（2026-10-26）。
- **C10**（子代理结论未二次核）→ **规避生效 1/1**：opencode 对 M1 与 HARD 项做实测/记录核对后
  才写结论；DSH 侧自评审发现逐条核实后才返工。
- **C17**（双轴子代理类型）→ **规避生效**：两轴均 `code-reviewer` 只读；DSH reviewer teammate
  亦只读。
- **C21**（取证命令文档停在单一 shell）→ **复现 1/2**：git-bash `/tmp` 映射误判（并入 C30）。
- **C22**（批量合并触发 concurrency 取消）→ **未命中**：opencode 等新 head CI 全绿才合下一张，
  master 三次 merge run 全 success。
- **C13**（merge 后 CI 静默不触发）→ **未命中**。
- **C11**（`gh run watch` 截断）→ **未命中**（用 `gh pr checks` 轮询）。
- **C18**（返工未复审就推送）→ **未命中**：返工 commit 与 LOW 修复均在独立评审复核后合并。

## 观察项（记录，不建议动）

- 本机验证成本基线：5 次全量 pytest（约 23 分钟）+ 13 次一次性 full-access 批准；full-access
  已尽量批处理（一次命令串 targeted → 3 连跑 → 全量）。多切片时全量按 PR 边界跑是「PR 证据」
  要求，暂不压缩。
- 独立评审成本基线（opencode）：3 个会话约 350k tokens / 143 次工具调用 / 1 次工具错误；
  双轴评审价值本次得到验证（0 HIGH，但纵深问题 #275/#276 由它发现）。
- 合并后遗留：本地 14 个已合并分支、远端 5 个已合并分支已在本会话清理（保留
  `prototype/frontend-redesign`；另清掉 3 个沙箱自锁空 `tmp*` 目录）；本文件是第一份 2026-10-31 retro 记录。

## 实施记录（2026-10-31，待用户拍板）

- **一批（仓库内，一个 PR 或 docs 直推）**：C23 新测试 + C24 新文档与 AGENTS 指针 +
  C25/C27/C29 workflow 条目 + C30 issue-tracker 小抄。
- **二批（需用户批准，用户级）**：C26 修改 `~/.agents/skills/code-review`。
- **三批（判断题 / 可选）**：C28 评审分工口径、C31 AGENTS.md 压缩。
