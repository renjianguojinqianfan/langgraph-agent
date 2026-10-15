# Retro 记录：langgraph-agent 转向 S0（2026-10-14）

> 会话范围：本仓库，一次长会话——架构转向的 grilling（**结论**：单一 loop + 计划/执行两模式取代完成
> 验证栈）→ 转向 ADR 0008 + 七片切法 → 一次性原型 → **S0 治理先行落地并合并**（PR #134，`a0770ca`）
> → 七片开票（#135–#140 + 跟进 #141）→ 手动补跑 master CI。
> 账本惯例与记账规则见 `2026-10-11-langgraph-agent-76.md`；本文件只记本会话**新候选**（编号接续
> C12 起）与观察项；未命中既有候选的复现计数（C6 见下，为**相邻实例**，不作正式加成）。
> **观察日志，不是决断——下列任何一条都未实施，等用户决断。**

## 候选（按严重度）

### C12. 删 / 改名 CI job 会在分支保护里留下死检查，把之后每一张 PR 都锁死 · Automated checks · 新

- **事实**：S0 按计划删掉 `ci.yml` 的 `guard-protected-files` job（本 PR 的判据之一就是「少一个 job」）。
  合并时 `gh pr merge 134 --merge` 被拒：「the base branch policy prohibits the merge」。查设置：
  `required_status_checks.contexts = ["Protected files guard (resilience/registry)"]`——**要求的正是刚被删掉的那
  个 job**，它永远不会再出现。于是不只本 PR：**之后每一张 PR 都会被这道墙挡住**，且
  `gh pr merge --auto` 也救不了（它要等的那个检查不会来）。
  **仓库侧没有任何东西知道分支保护的存在**：`ci.yml` 一改，设置里的那个引用原地变成悬空指针；而
  `docs/agents/issue-tracker.md` 的「合并方式」只写了合并姿势（squash / 删分支核对），没提必需检查这一面。
  本 PR 的 check 列表上 `guard-protected-files` 确实消失了（判据成立），但**判据成立与「PR 还能合」是两件事**，
  本会话把两者之间的这个洞当场踩了。
- **修法方向**（判断题，落文档 + 贴命令）：`docs/agents/issue-tracker.md`「Release conventions」的「合并方式」
  加一句——**凡删 / 改名 CI job 的 PR，同批核一次分支保护的必需检查**（贴命令：
  `gh api repos/<owner>/<repo>/branches/master/protection/required_status_checks`，改法用 `--input` JSON，
  见 C15）。**机械门不做**：`GITHUB_TOKEN` 默认 `contents: read`，读不了 branch protection，加这个检查要
  上一个 admin PAT，成本大于收益。本次实修已在 PR #134 留下评论（含原状态与可逆命令）。

### C14. 「验证」被降级成「语法 / 解析通过」就报完成 · No-ops / 验证纪律 · 新

- **事实**：交付 `prototype/plan-build-modes.html` 前，我只跑了**语法**检查（`new Function(shell 源码)` 解析通过 +
  文件大小）就报「做好了，已经帮你打开浏览器」。实际页面一加载即 `ReferenceError`：shell 引用了纯逻辑模块
  **内部未导出**的 `RUNNING` / `PARKED` / `ERROR`（那是 classic script 共享作用域之外的 IIFE 局部名），
  于是整段脚本死掉、**所有按钮的点击监听根本没注册**。用户回来报告「点不动这些」——**由用户发现**。
  事后搭了个极简 DOM 桩真跑一遍（含模拟点击）才定位到，随后的烟测（加载 / 点场景 / 乱序提示 / 拒绝路径，
  9 项）全过。
  这是「自评绿了但真的没验」类失败的一次实例，且**我的自检比我的声明弱**：我把「能解析」当成了「能跑」。
  与控制台输出无关（浏览器控制台会报错，但 agent 看不见——与既有候选 C6「UI 验收没有 agent 可用的信息通道」
  同源；C6 记的是应用前端，本次是一次性原型，故只作**相邻实例**、不作 C6 的正式加成）。
- **修法方向**（跨项目规则，本仓 + 全局 `~/.agents/AGENTS.md` §7）：**凡声称「验证过」，必须至少触发一次真实
  执行路径**；纯解析 / 编译 / 静态检查只能称「语法通过」，不得当作运行期证据。无测试框架的单文件产物
  （HTML 原型这类）最小执行路径 = 起 DOM 桩加载一次 + 模拟一次主交互。这条与全局 §3「'verify 过了'也≠完成」
  是同一族，但补上了缺的那半：**验证的强度必须匹配声明的强度**。

### C13. merge 到 master 后 push 事件可能静默不触发 CI，「护 master 全绿」随之静默失效 · Automated checks · 新

- **事实**：PR #134 合并后（master `a0770ca`），**该 commit 上 0 个 check-run**，master 的 run 列表仍停在合并前的
  `9827c65`；而**同一仓库** #130 的 merge commit（`9827c65`）**有** push run——所以不是「merge commit 一律不触发」。
  `push` 的 `paths` 白名单 `["**", "!docs/**", "!*.md", "docs/architecture.md"]` 按读法应当匹配 `ci.yml` /
  `pyproject.toml`（本次改动的两个非文档文件）。**原因未定**，本记录不写成因结论（未证伪的解释一律不落地）。
  影响面：`AGENTS.md`「护 master 全绿」的主张挂在 push 这条路上，而它可能**静默**不跑（不报错、只是没有 run）。
  本会话用 `gh workflow run ci.yml --ref master` 手动补绿（run 38592159452 `success`，三 job 全过、live-e2e 按设计跳过）。
- **修法方向**（贴命令，不写工具）：合 master 后加一步核——`gh run list --branch master --limit 1 --json headSha,status`，
  headSha 不等于本次 merge 就手动 dispatch 补跑。原因是**独立待查项**（可另开票；本会话未查完）。

### C15. `gh api` 写请求的两个坑（`-f` 全当字符串 / 数组形态）· Tool economy · 新

- **事实**：给分支保护改必需检查时，`gh api -X PATCH … -f 'strict=false'` 被 **422** 拒：
  「For 'properties/strict', "false" is not a boolean」——`-f` 把一切值当字符串发。改用
  `--input <utf8 json 文件>` 一次成功（`{"strict": false, "contexts": [...]}`）。同时 `-f "contexts[]=x"` 的
  数组形态本会话未验证，绕开了。
- **修法方向**（并入 C8 那份「本机 pwsh 取证 / 命令」贴命令清单）：**`gh api` 的写请求一律用
  `--input <json 文件>`**（布尔、数组、嵌套对象都稳），读请求用 `--jq`；`-f` 只用于确实要字符串的场景。

## 观察项（本会话记录，不建议动）

- **`paths` 白名单把 `docs/**` 与根 `*.md` 排除在外，本次救了「文档大改但不想跑全套 CI」的场景**：
  S0 的 14 个文件里 12 个是文档，真正触发面只有 `ci.yml` + `pyproject.toml`。既有规则按设计生效。
- **C8 的「落盘 + `Read -Encoding UTF8`」模式本次全程生效**：gh 的中文输出（PR checks / run 列表 / issue 创建 /
  run view）再未出现乱码。可作默认姿势（C8 已 2/2 待决，不重复计数）。
- **C11（`gh run watch` 收尾输出被截断）本会话被规避**：没用 `watch`，改为「脚本内轮询 `gh run view --json status`」
  一次跑完（8 次轮询拿到终态），无需二次取证。属规避形态，不作加成。
- **pwsh 缺 unix 工具**：会话开头两条命令因 `wc` / `head` 不存在而失败（「The term 'wc' is not recognized」），
  换来 `Measure-Object -Line` / `Select-Object -First`。单次成本小、与 C8 同机同族，不另立候选。
- **转向会话自身的规格化有效**：grilling 逐问逐答 + ADR 0008 + `docs/pivot-plan-build.md` 三段分离
  （决策 / 切法 / 票）使得「片号 ↔ 票号」能一次接上（`AGENTS.md` 里已写 〔片 Sx〕，票号回填进切法文档）。
  属设计生效，无候选。

## 复现记账规则

- 沿用 `2026-10-11-langgraph-agent-76.md` 的规则：同候选再命中往该文件对应条目加复现计数；到 2/2 提请用户决断；
  新候选继续 C16 起编号，严重度重排全表。
- 本会话未对既有候选作正式加成（C6 仅相邻实例，理由见 C14；C8 / C11 为规避形态，见观察项）。
