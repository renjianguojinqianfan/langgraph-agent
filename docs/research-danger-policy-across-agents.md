# 危险动作拦截：四家参照物对标（codex / opencode / dsh / pi）

> 2026-10-16。**用途**：S2（[#136](https://github.com/renjianguojinqianfan/langgraph-agent/issues/136)）「设置里的规则表 + 一个钩子」的拍板依据——
> 它回答「别人家有没有这种东西、长什么样、我们哪几条有先例、哪几条是有意分歧」。
> 决策本体见 [ADR 0008](adr/0008-single-loop-plan-build-modes.md) 第 6 条与对照表；切法见 [pivot-plan-build](pivot-plan-build.md) §S2；
> pi 的逐行实证另见 [research-pi-agent-construction](research-pi-agent-construction.md) §5（本文不复制它的内容，只取结论）。
> 横向能力对标（粒度到「审批模式」）见 [agent-comparison-report](agent-comparison-report.md)。

## 0. 取证边界与置信度

**取的什么**：每家都取「工具/命令**执行前**的判定位 + 喂给它的配置面」，以**文件级 blob SHA** 锚定（`?ref=<sha>` 可逐字节复核）。

| 家 | 仓库 / 取值 | 读到的文件（blob SHA 前 12 位） |
|---|---|---|
| codex | `openai/codex` 默认分支 | `codex-rs/execpolicy/README.md` `557e46fbed39`、`execpolicy/src/decision.rs` `15dc1f711d78`、`execpolicy/examples/example.codexpolicy` `4040f612092e`、`core/src/exec_policy.rs`（只取命中行，未整读） |
| opencode | `anomalyco/opencode` `dev` 分支 | `packages/web/src/content/docs/permissions.mdx` `eb8975a882c7`、`packages/core/src/permission.ts` `2aecebb0ce34`（只读 import 段）；`packages/core/src/v1/config/permission.ts` 与 `packages/opencode/src/config/config.ts` 只通过 **Context7 文档片段**（随片段带源链接，未逐行读） |
| dsh | `deepseek-ai/deepseek-harness` `master` | `docs/subsystems/approval.md` `5a2513775a58`、`docs/subsystems/permission-presets.md` `e5fa61c4058d`、`packages/interaction/user-approval/README.md` `8c52fa0905c1` |
| pi | `earendil-works/pi` | `examples/extensions/permission-gate.ts` `ce29f7eb5cf9`、`examples/extensions/plan-mode/utils.ts` `62123f9e3996`（这两个文件在 v1.0.4→v1.2.0 逐 tag 逐字节相同，见 [research-pi-agent-construction](research-pi-agent-construction.md) [27]） |

**没做的**：**没有锚 release/tag**（除 pi 引仓内调研的 v1.2.0 结论），取的是各仓默认分支的文件级快照——引用随 SHA 可复核，但它们**不等于**某个已发布版本的行为。各家**未核**项集中列在 §3.4。

## 1. 怎么收敛（方法，不是「把四个名字摆一起看」）

1. **先定比较单位——按功能位，不按名字**。判据是「工具执行前，有没有一个由外部配置/规则驱动、结果至少分『放行 / 问人』的判定位」。名字（`execpolicy` / `permission` / `presets` / `tool_call`）不参与归并：dsh 那张「表」根本不判命令，只看名字会把假共性收进来。
2. **固定问卷逐家问，答不出写「无此物 / 未核」**，不许用「名字像」代替「机制同」。本文用的十条轴：判定点在哪 / 判定输入是什么 / 结果几档及语义 / 有没有理由文本 / 配置从哪来 / 未配置时默认 / 配错时朝向 / 拒绝后模型看到什么 / 与人批的关系 / 判定粒度。
3. **按覆盖率定级，先怀疑粒度再怀疑结论**：4/4 才叫共性，3/4 叫多数派。某轴只有 2/4 时先问「是否轴切太细」——把「有没有逐命令表」抬到「有没有配置面来定放行/问人」，四家才都可答。
4. **分歧拆两类**：有意分歧（本仓 ADR 明写的）与未核/说不清（信息缺口）。未核**不许**写成共性。

## 2. 逐家事实

### 2.1 codex —— `codexpolicy` 策略文件（最接近本仓形态）

- **表**：Starlark 语法，规则是 `prefix_rule(pattern=["cmd", ["alt1","alt2"]], decision=…, justification=…, match=[…], not_match=[…])`；另有 `host_executable(name, paths)` 约束「哪些绝对路径可回退到 basename 规则」。`decision` **默认 `allow`**，取值 `allow` / `prompt` / `forbidden`。
- **判什么**：命令的 **token 前缀**（有序，列表项表示可选）；`/usr/bin/git status` 先按精确首 token 匹配，再按 `host_executable` 规则回退到 `git`。
- **三档语义**（`decision.rs` 注释原文）：`Allow` = 无需进一步批准即可运行；`Prompt` = 请求用户明确批准、**在 `approval_policy="never"` 下直接拒绝**；`Forbidden` = **不予考虑即拦**（blocked without further consideration）。
- **理由文本**：`justification` 是可选的人读理由，**可出现在审批提示或拒绝消息里**；`decision="forbidden"` 时鼓励把替代方案写进去（如「Use `jj` instead of `git`」）。`core/src/exec_policy.rs` 另有常量拒绝理由（`REJECT_RULES_APPROVAL_REASON` / `REJECT_SANDBOX_APPROVAL_REASON`）与 `prompt_is_rejected_by_policy()`。
- **配置校验**：`match` / `not_match` 是**加载时校验**的示例调用（「think of them as unit tests」）。
- **示例**：`example.codexpolicy` 里 `git reset --hard` → `forbidden`（justification「destructive operation」），`cp` → `prompt`，`ls` / `cat` / `pwd` / `which` 走默认 `allow`。

### 2.2 opencode —— `opencode.json` 的 `permission`（粒度最细）

- **表**：`permission` 分工具级（`"edit": "deny"`）与**对象语法**（按工具输入分档）：`bash` 按命令通配（`{"*":"ask","git *":"allow","rm *":"deny"}`）、`edit` 按路径、`webfetch` 按 URL、`skill` 按名字通配（`"internal-*":"deny"`）；另有 `external_directory`（工作目录外的路径）。
- **判定**：每条规则解析成 `allow`（不问直接跑）/ `ask`（要批准）/ `deny`（拦）。**最后一条命中的规则赢**（文档教你 `*` 写最前）；通配符是 `*` / `?`（**不是正则**），支持 `~` / `$HOME` 前缀。
- **与旧配置的关系**：`tools` 布尔配置（`{"tools": {"edit": false}}`）会**合并进** `permission`（`false`→`deny`，`write`/`edit`/`patch` 归一到 `edit` 键）；v1.1.1 起 `tools` 弃用。
- **自动模式**：`--auto` 只放行「本来会问」的请求，**显式 `deny` 照样执行**。
- **可扩展**：`permission` schema 用 `Record(string, Rule)` 收尾，插件/自定义工具也能有键。

### 2.3 dsh —— 预设表 + 审批缝（表管「问不问人」，不管「哪条命令拒」）

- **表**：`permission-presets` 的预设表把两个独立旋钮打捆成命名预设：`sandbox/mode` × `approval/policy`。出厂表是 `workspace-write`（`workspace-write` + `ask`）与 `danger-full-access`（`danger-full-access` + `never`）；`custom` / `auto` 是保留名。**这张表不判单条命令。**
- **判定的产物**：`user-approval` 缝给出**闭集** `allowed-once | rejected | cancelled | unavailable`；缺答案者/抛错/不合规 → `unavailable`，调用方**fail-closed**（只有 `allowed-once` 才放行，且**只对这一次请求**有效）。
- **策略**：`ApprovalPolicy` 只两值——`ask`（发给答案者链，没有答案时默认 `unavailable`）与 `never`（**不发问，确定性拒绝**）。生效值取会话日志里最后一条 `approval/policy`，回退到服务配置（所以 replay 能重建）。
- **配错朝向**：预设表配错（含保留名、或叠在不能约束沙箱的执行器上）**在插件加载时抛**。
- **模型看到什么**（README 原文）：模型看得到**工具的结果与当前策略**，看不到人的审批 UI 与审计事件。

### 2.4 pi —— 内置没有表，只有位置（钩子）+ 示例里的硬编码表

- **内置**：pi **不内置**逐调用审批（`security.md` 原话「does not ask for approval before every tool call」）；`tool_call` 钩子是那个**位置**，签名返回 `{block?, reason?, terminate?}`（[research-pi-agent-construction](research-pi-agent-construction.md) §5.2）。
- **示例表**（非内置）：
  - `permission-gate.ts`：硬编码三条正则（`rm -rf` / `sudo` / `chmod|chown 777`），命中后**有 UI 就问人、无 UI 直接 `block`**（fail-closed），人答 No 也 `block` 并带 reason；
  - `plan-mode/utils.ts`：`DESTRUCTIVE_PATTERNS`（约 40 条：写类、包管理、git 写、进程/系统、编辑器……）+ `SAFE_PATTERNS`（约 50 条只读命令）两张大表，`isSafeCommand()` 先命中毒性表即判不安全。
- **钩子的既有约束**（本仓有意不抄的部分）：能**原地改参数且改完不复核**；hook 抛错 = **fail-safe 拦下**。

## 3. 收敛结果

### 3.1 共性（4/4）

| # | 共性 | 各家的落点 |
|---|---|---|
| 1 | 判定位在**执行前**，不是事后审计 | codex `execpolicy` 判定后才跑；opencode `permission` 决定「自动跑 / 问 / 拦」；dsh 工具把 `ask` 路由过审批缝、**缺缝即 fail closed**；pi `tool_call` 在参数校验后、执行前 |
| 2 | 结果至少含**「放行」与「拒」两档**，且都有「人批」这一档的落点 | codex `allow`/`prompt`/`forbidden`；opencode `allow`/`ask`/`deny`；dsh `ask`（人或机器答）+ 闭集 outcome；pi 无返回=放行 / 问人 / `block` |
| 3 | 判定逻辑**不写死在 loop 里** | codex 独立 crate + 策略文件；opencode config→规则集；dsh 可选插件层；pi 扩展钩子 |
| 4 | 拒绝 = **一次工具结果**，任务继续，模型看得见 | dsh 原文「模型看得到 resulting tool outcome，看不到审批 UI/审计」；pi「否 = block → 错误结果 → 模型继续」；本仓 S2 同形 |
| 5 | 人批是**一次性**的，不是永久放行 | dsh `allowed-once`（只对这一次请求）；codex `prompt` 逐次；opencode `--auto` 只改「本来要问的」；本仓逐调用 |

### 3.2 多数派（3/4，够拍板用）

- **配错 / 兜底朝向 = fail-closed**：dsh（缺/坏答案者 → `unavailable` → 拒；预设配错**加载时抛**）、pi（无 UI → 直接 `block`）、codex（`prompt` 在 `approval_policy="never"` 下变拒绝；策略文件解析失败即报错）。**opencode 未核**。
- **拒绝带理由文本**：codex（`justification` + 常量拒绝理由）、pi（`{block, reason}`）、本仓（`reason`）；opencode / dsh **未见**理由字段（dsh 明说不把审计/UI 给模型，只给 outcome）。

### 3.3 分歧（不是共性，别硬凑）

- **「事先写好、机器直接拒」的独立档**：codex ✅（`forbidden`）、opencode ✅（`deny`）；dsh 只有**会话级** `never`（把 ask 压成 reject）、pi 靠钩子代码返回 `block` —— 形态不同，2/4 有独立档。
- **判定粒度**：三家都只盯 **shell 命令**（codex token 前缀 / opencode 命令通配 / pi 正则打在 command 上）；本仓按**任意工具 + 入参**判。
- **匹配方式与优先级**：codex token 前缀 / opencode `*`·`?` 通配且**最后一条命中赢** / 本仓**入参字符串值上的正则**且**第一条命中赢**。
- **有意分歧**（ADR 0008 明写，非疏漏）：能不能改参数（只有 pi 能，且改完不复核）、有没有停顿态（dsh / opencode 阻塞问人；本仓无停顿态，拒绝即一次工具结果）。

### 3.4 未核（信息缺口，不许当共性用）

1. opencode：配置写坏时的朝向（fail-closed 还是回退）。
2. opencode：拒绝时模型是否拿到理由（文档只讲三档语义）。
3. codex：`forbidden` 之后模型收到的**具体形状**（只拿到「有常量拒绝理由」与 `prompt_is_rejected_by_policy`）。
4. dsh：审批请求里是否带理由字段（文档层面未见）。
5. 全部四家：均**未锚 tag/release**，见 §0。

## 4. 对本仓 S2 的意义

| S2 的取舍 | 由哪条事实支撑 |
|---|---|
| 「设置里的表 + 命中即拒 + 理由回模型」 | codex 的 `forbidden` + `justification` 是最接近的既有形态（3.3 的独立档之一）；「理由回模型」有 codex / pi 两家先例（3.2） |
| **拦截不是安全边界**（跟 `mcp_force_confirm` 同一立场，真边界在子代理收窄/隔离） | 四家里只有 codex 有「机器直接拒」的独立档，其余默认都是**问人**；且它们的真边界都落在沙箱（dsh `sandbox/mode`、codex sandbox、opencode `--auto` 不越 `deny`） |
| 被拒调用**不加新字段**，落在现成的「没执行」两格（`status:"skipped"` + `error`） | 四家都把「表」的产物落在**配置**与**判定结果**上，**没有一家**在调用记录上加「被规则拒」这类布尔字段 |
| **表写坏时的朝向** | 3.2 第一条：有证据的三家全是 **fail-closed**（dsh 加载即抛/拒，pi 无 UI 即 block，codex 解析失败即报错）；**opencode 未核**。本仓**已定软档**（整表写坏 warning + 照跑），见 §4.1 |
| 粒度选「正则打在入参的**文本叶子**上」 | 这是**我们的形状**：三家只看 shell 命令。若评审嫌糙，opencode 的「通配符 + 最后匹配赢」是有先例的替代（3.3）。**2026-10-16 据独立评审 R2 改**：从「搜 `json.dumps(args)` 整段文本」改成「逐个文本叶子（字符串值 + 数字/布尔的文本形式）search」——JSON 转义会把真 tab / 引号 / Windows 路径变形，照着真实命令写的模式反而漏拦（实测见 PR #144 评审评论）。**限制**：不跨叶子拼接，argv 写成 `["rm","-rf","/"]` 时命不中（旧实现同样命不中） |

### 4.1 唯一还悬着的行为选择：规则表写坏时 —— **已定（2026-10-16）：软档**

现状（本片）：单条写坏 → warning 后作废；整表不是合法 JSON 数组 → 整表作废（= 不拦）。
参照物给出的选项与本次选择：

- ✅ **选了「软」**：整表写坏也 **warning**（`DangerPolicy.from_settings` 里一句，不动共享的 `_parse_json_list`）——**出声但照跑**；
- ❌ 未取「硬」：与 dsh 同口径（配置写坏**拒绝启动**）会更接近 3/4 参照物，但对本地 demo 平台太重，且票面 AC 没要；
- `""` / `[]` / `null`（含空白）是「我就是要空表」，**不算写坏、不出声**（否则每台没配表的机器都刷 warning）。

软档下「整表写坏」的实际后果仍是**不拦**（只是不再静默）——这一点在选硬档之前一直成立，别把它当安全保障读。

## 5. 怎么复核 / 下次怎么增量

- **复核**：按 §0 的 blob SHA 取原文，例如
  `gh api repos/openai/codex/contents/codex-rs/execpolicy/README.md?ref=557e46fbed39631bd84eba696254db78cbc20873 --jq .content | base64 -d`
  （opencode 的 `permissions.mdx`、dsh 的 `approval.md` 同理；pi 见仓内 [research-pi-agent-construction](research-pi-agent-construction.md) 的引用表）。
- **增量**：补齐 §3.4 的四个未核项；若要跟到新版本，**重新取 SHA** 而不是改旧结论——本文是**带日期的快照**（同 `docs/` 里其他调研文档的规矩：不改写历史，新结论另开一篇或加日期段）。

## 6. 补记（2026-10-16）：未配置时默认

> 回答 D1（S4 删掉确认闸门之后，`policy_rules` 默认空表 = 不拦，要不要随片附一份默认规则）。
> §1 把「未配置时默认」列进了问卷，§2 没答完。本节只补这一轴，不改上面的快照。
> 仍是默认分支的文件级快照，**没有锚 release/tag**。

| 家 | 本轮新读的文件（blob SHA） |
|---|---|
| codex | `protocol.rs` `e2457f810461`（`AskForApproval`，`OnRequest` 带 `#[default]`）；`core/src/config/mod.rs` `20939cf80bc7`（受信任 → `OnRequest`，不受信任 → `UnlessTrusted`，否则枚举默认）；`approval-presets/src/lib.rs` `c7ce9e578b`（标成 Default 的预设）；`core/src/exec_policy.rs` `7e307e4f4aaa`（空策略、未命中时的判定）；`shell-command/.../is_dangerous_command.rs` `2440f17001da`；`windows_dangerous_commands.rs` `8072b881b130`（只读到强制删除与 URL 启动这一类，未逐条读完） |
| opencode | `permissions.mdx` `eb8975a882c7`；`packages/schema/src/agent.ts` `c47d5e8e7ef8`；`packages/core/src/plugin/plan.ts` `ef9ed2422a2e` |
| dsh | `user-approval/src/index.ts` `9a14f04d0b3c`（`policy` 默认 `'ask'`）；预设与审批文档 SHA 同 §0；`tool-bash/README.md` `518a0887f63e` |
| pi | `security.md` `4f0dc290fe10`；示例不是出厂，见仓内 [research-pi-agent-construction](research-pi-agent-construction.md) |

### 6.1 四家出厂时实际做什么

- **codex 默认是「问人 + 沙箱」，不是一份出厂黑名单。** 没配策略文件时 `ExecPolicyManager` 的默认是 `Policy::empty()`。仓库里搜不到名为 `default.rules` 的文件；README 里的 `src/default.rules` 只是 CLI 示例路径。没有规则命中时，README 写 `decision` 省略。另外有一份**硬编码、很小**的危险命令判断：POSIX 侧只认 `rm` 带 force（会剥掉 `sudo` / `env` / `trap` 再看）；Windows 侧还认 `Remove-Item -Force` 一类和带 URL 的启动。注释原文：优先问人，不直接禁；用户把提问关掉（`Never`）才 `Forbidden`。没被标成危险的命令，在受限沙箱里直接跑，让沙箱去拦；不受限环境也直接跑。标成 Default 的预设文案是：工作区内可读写、可跑命令，上网或改工作区外的文件要批准。
- **opencode 默认几乎全放行。** 代码里新 agent 的权限是 `*` = `allow`，再加 `.env` 读 = `ask`（文档写的是 `deny`，与代码不一致，以这份代码为准）。文档还说 `doom_loop` 和 `external_directory` 默认 `ask`。`rm *` / `git push *` 那些 deny 是文档里的**配置示例**，不是出厂表。计划模式插件只给 `edit` 加了「目录外 deny、计划目录内 allow」，文件里没有 `bash` / `shell` 规则。
- **dsh 默认是问人，表不判命令。** `ApprovalService` 的 `policy` 默认 `'ask'`。出厂预设是 `workspace-write` + `ask`；`danger-full-access` + `never` 是另一档，不是默认。`never` 的语义是「每一次询问都直接 `rejected`」，不是「全部放行」。bash 先在沙箱里跑，沙箱拒绝后可以带理由升级再问一次人；没有答案者就 fail-closed。
- **pi 出厂不问、也不拦。** `security.md` 写明不会在每次工具调用前要批准，真边界是操作系统用户或容器。示例里的正则表不随产品加载。

### 6.2 对 D1 的含义（事实，不是拍板）

四家里**没有一家**把「出厂一份可配置的危险命令黑名单」当默认安全措施。也**没有一家**的出厂默认是「空表 = 危险动作直接跑」——除了 pi，以及 opencode 对 bash 的放行。codex / dsh 的出厂默认是问人，沙箱才是硬边界；把提问关掉是显式的另一档（codex `Never` + 不受限，dsh `danger-full-access` + `never`）。

本仓 ADR 0008 第 6 条已经决定不弹框，所以**抄不了「默认问人」**。在「不能问人」这个前提下，对照物的对应物是：

- codex：提问关掉之后，被标成危险的那一小类变成直接拒，其余靠沙箱。那一小类不是 HTTP 写、也不是 git。
- dsh：`never` 把询问变成拒绝，不是放行。
- pi：承认不拦，把边界写在容器上。
- opencode 的 `--auto` 把「本来要问的」放行、显式 deny 仍拒——这是用户显式打开的更松一档。

因此：(a)「随 S4 附一份覆盖 `code_exec` / HTTP 写的默认规则表」没有出厂先例；(b)「空表不拦，写进 README」对齐的是 pi，比 codex / dsh 的出厂默认更松。两边都不是「别人家的默认安全措施」。别人家在不能问人时真正靠的是沙箱或进程边界，不是正则表。

### 6.3 本节未核

1. 全部四家仍未锚 tag/release。
2. opencode：文档写 `.env` 为 deny，代码是 ask。计划模式 README 检索片段写「跑 bash 前要问」，`plan.ts` 里没有 bash 规则；未全仓再搜是不是别处补上了。
3. codex：`workspace-write` 预设与 `FileSystemSandboxKind::Restricted` 的逐行对应没对完；Windows 危险命令文件未逐条读完。POSIX 侧读完了，只有强制 `rm`。
4. dsh：`danger-full-access` 下 bash 是否还会走进审批缝，未逐行追。
