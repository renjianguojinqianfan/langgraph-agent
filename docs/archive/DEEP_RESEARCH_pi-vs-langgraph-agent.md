# Deep Research：pi 的极简 harness 与 langgraph-agent 的架构差距

> 生成 2026-09-24 ｜ 深度：deep ｜ 来源：42（一手仓库/官方文档为主）｜ 子代理：4 检索 + 2 补口 + 1 引用核对
> **立场声明**：本报告**只做调研与差距分析，不提修改方案**。所有"要不要改"的判断留给读者。LangGraph / LangChain 视为既定底座，不重开该议题。
> pi 证据基准：`earendil-works/pi` 发布标签 **v0.87.1**（`@earendil-works/pi-coding-agent@0.87.1`，发布于 2026-09-22T19:43:43Z）；`docs/*.md` 读自 `main`（HEAD `8676a0dc`，2026-09-24）。本仓库取证锚：`master @ 4857f03`。

## TL;DR

pi 与我们的差距**不是"框架 vs 不用框架"**，而是同一层（harness）上两个端点的取舍：pi 把循环、四个工具、上下文树做到极致透明，然后把权限、子代理、todo、plan mode、沙箱**全部推到扩展层与容器层**；我们把同样的职责**硬编码进 `nodes.py` / `context.py` / `resilience.py` / `snapshots.py`，并只留下"加工具"这一条扩展缝**。真正刺眼的差距不是功能数量，而是**我们的能力没有对应的缝**——加一个 git 动词、一个新节点、一种新事件都要改核心文件。

## 执行摘要

三句话可以概括全部调研结论。

第一，**术语必须先钉住，否则整场讨论会跑偏**。用户提供的锚定教材把 harness 放在第三层（Runtime＝LangGraph → Framework＝LangChain → Harness＝deepagents）[13]；LangChain 官方博客用二分定义（"harness 就是除了模型之外的一切代码、配置与执行逻辑"）[14]。两种用法粒度不同。本报告采用**三层表述**，因为 LangChain 自己在 2026-08 的文章里就是这么划的："LangGraph 是 agent runtime，LangChain 是 agent framework，Deep Agents 是 agent harness"，并明确"runtime 给最多控制、最少抽象；harness 相反" [41]。**结论：pi 和 deepagents 在同一层**，差别只在各自实现了多少条 harness 职责 [16][41]。

第二，**pi 的"极简"是有立场的工程，不是没做完**。它的循环是 `packages/agent/src/agent-loop.ts` 里一个手写 `while (true)`，只 import 自家 `pi-ai`，零第三方 agent 框架 [3]；内置工具不享有特权，`createBashTool` 配可替换的 `*Operations` 接口，示例扩展可以整个换掉 bash [5][9]；被拒绝的能力有原话、有理由、多数还有 example 替代物 [10]。第三方独立观察者 Armin Ronacher 补了一条 pi 自己没写的第一性理由：MCP 工具集在会话开始时烘进系统上下文，热重载会**摧毁 prompt cache** [11]。

第三，**"厚"到底值不值，目前没有实测**。LangGraph 的 runtime 作者 Nuno Campos 承认"任何代码框架最大的对手永远是'不用框架'"，同时给出交换条件：裸 while 循环"就没办法实现 checkpointing 或 human-in-the-loop" [40]；LangChain 1.0 发布文自认"抽象有时过于厚重、包面积失控" [17]。而 Chroma 的受控研究显示上下文变长确实让 18 个模型的表现非一致退化 [37]，Anthropic 据此推荐压缩、外部记忆与子代理 [38]——但**没有任何一项研究把"编排框架"与"裸循环"放在同一条件下对比过**。这条否定性发现决定了：本报告不能给出"我们该往简还是往厚走"的结论，只能把差距摆清楚。

---

## 1. 共同语言：harness 是哪一层 ［置信度：High］

### 1.1 两种定义，取哪一个

用户指定的锚定教材《Deep Agents 实战》第一章把 agent 系统切成三层：**Runtime**（LangGraph，也举 Temporal、Inngest）负责 durable execution、状态、流式、HITL；**Framework**（LangChain 1.0，也举 Vercel AI SDK、CrewAI、OpenAI Agents SDK、Google ADK、LlamaIndex）负责模型/工具抽象与 agent 循环；**Harness**（Deep Agents，也举 Claude Agent SDK、Codex SDK）是"在 Runtime 与 Framework 之上预装了经过验证的工具接口与中间件套件的开箱即用 Agent 套件" [13]。该章用车间比喻：harness 给的是"一间工具挂在墙上、流程写在白板上的完整工具房"，并列出四项预设能力：虚拟文件系统工具（`read_file`/`write_file`/`edit_file`/`delete`/`ls`/`glob`/`grep`）、任务规划（`TodoListMiddleware` → `write_todos`）、子代理委派（`task` 工具）、长期记忆（LangGraph Memory Store）[13]。

LangChain 官方博客《The Anatomy of an Agent Harness》用的是**二分**定义："A harness is every piece of code, configuration, and execution logic that isn't the model itself"，"Agent = Model + Harness"，并把系统提示、工具/技能/MCP 及其描述、文件系统与沙箱等基础设施、子代理派发与模型路由等编排逻辑、以及做压缩/续跑/lint 的钩子中间件全部算进 harness [14]。作者自己承认边界是糊的："There are many messy ways to split the boundaries of an agent system between the model and the harness." [14]

**本报告取三层表述**，理由不是审美，而是 [41] 显示 LangChain 自己在 2026-08 就采用了"harness"这个词来命名它最上面那层——也就是 pi 所占据的那一层。用对手方的词汇表，比较才不会变成鸡同鸭讲。

### 1.2 一个必须先说的自我修正

本仓库 `requirements.txt` 明确写着 **0 处 import `langchain*`**（`backend/` 全域），AGENTS.md 亦规定 `langchain*` 保持零钉版（代码取证）。这意味着：**LangChain 那一层的 middleware 能力（`wrap_tool_call`、`after_model`、`HumanInTheLoopMiddleware`、`SummarizationMiddleware` 等 [19][42]）我们一条都没有用**。

这条事实很重要，因为它决定差距表怎么读：我们的确认闸门、上下文压缩、重试熔断、完成校验、回滚账本，全部是**手写在 harness 层**的实现（`nodes.py:551-620`、`context.py`、`resilience.py`、`snapshots.py`），不是从框架继承的。把"框架有这能力"记成"我们有这能力"，是这类对比最常见的错误。

### 1.3 场景不对等声明（红队）

pi 是**单人、单终端、单会话**的编码工具；我们是**多客户端服务端平台**（FastAPI + SSE + 鉴权 + 前端工作台）。pi 没有回滚账本、没有多租户鉴权、没有事件流回放，因为它的场景不需要。因此第 5 节的每一条差距都必须落到三类之一，否则就是拿苹果比橘子：

- **A 类｜场景差异导致的合理缺失**：对方没有，是因为我们的场景它没有。
- **B 类｜我们缺扩展缝、只能硬编码造成的债**：能力本身合理，但实现方式让它不可替换。
- **C 类｜我们主动更厚**：对方用脚投过票并给出理由，我们选了相反方向。

---

## 2. pi 的骨架 ［置信度：High］

### 2.1 包拆分与依赖方向

pi 是一个 12 包的 TypeScript monorepo，依赖方向严格单向、无环：`pi-ai`（统一多供应商 LLM API）→ `pi-agent-core`（有状态循环）→ `pi-coding-agent`（CLI + 内置工具 + 会话树 + 扩展运行时）；`pi-tui`、`pi-durable`、`pi-protocol`/`pi-client`/`pi-server`、`pi-telemetry`、`chord` 作为兄弟包存在 [4]。根 README 把仓库描述为"AI agent toolkit: unified LLM API, agent loop, TUI, coding agent CLI"，对外公开的包是七个 [1]。

依赖面本身就是立场：`packages/ai/package.json`（v0.87.1）的 dependencies 逐条为 `@anthropic-ai/sdk 0.124.0`、`@aws-sdk/client-bedrock-runtime 3.1127.0`、`@google/genai 2.21.0`、`openai 6.40.0`、`typebox 1.3.27`，外加 `partial-json`、两个 proxy-agent 与自家 `pi-telemetry`——**没有 LangGraph，没有 LangChain，没有 Vercel AI SDK** [1][4]。供应链上还有一层硬化：直接依赖钉到精确版本、`.npmrc` 开 `save-exact=true` 与 `min-release-age=2`、发布时带 `npm-shrinkwrap.json`、对依赖的生命周期脚本走 allowlist [1]。

### 2.2 循环：一个手写 while(true)，且故意不给 max-steps

`packages/agent/src/agent-loop.ts` 是显式的 `while (true)`，配 `config.getSteeringMessages()`、`prepareNextTurn`、`prepareRequest`、`finishTurn`、`transformContext`、`executeToolCalls` 六个可注入环节；外层循环用于"agent 本该停下时又有排队消息进来"，内层 `while (hasMoreToolCalls || pendingMessages.length > 0)` 驱动工具轮次 [3]。事件序列是 `agent_start / turn_start / message_start / message_update / message_end / tool_execution_* / turn_end / agent_end` [3]。工具执行模式可配 `parallel`（默认）或 `sequential` [3]。

值得单独记一笔的是作者对"旋钮"的态度：**循环不提供 max-steps**——"The agent loop doesn't let you specify max steps or similar knobs. I never found a use case for that, so why add it?" [10]。我们这边是 `max_steps=15` 加上 `recursion_limit = max_steps*8+20`（子任务 `*4+10`），属于同一议题的反面选择（C 类）。

### 2.3 扩展模型：产品面就是扩展 API

扩展是一个 TypeScript 模块，经 `jiti` 加载、**可热重载、与主进程同权限同进程运行**；工厂函数收到一个 `ExtensionAPI` 对象，可以 `registerTool` / `registerCommand` / `registerShortcut` / `registerFlag` / `registerProvider`，订阅生命周期事件，并通过 `ctx.ui` 驱动对话框、组件与自定义 widget [5]。

事件按语义分两类：**notify**（通知）与 **transform/cancel**（可改写或取消）。关键几条：`tool_call` **可以改写入参或直接 block**，且"一个 `tool_call` 处理器失败会把该工具作为 fail-safe 拦下"；`tool_result` 处理器**可组合**，每个处理器能看到前一个的改动；`context` / `context_with_system` 可以改写送给模型的转录；`turn_end` 与 `agent_before_settle` 是唯二"可操作"的收尾边界，能追加条目并请求一次续跑 [5]。

**内置工具不是特权路径**：每个内置工具都以 `createBashTool` / `createReadTool` 等形式导出，并配一个可替换的 `*Operations` 接口（`createLocalBashOperations` 是默认实现），于是 `ssh.ts` 与 `sandbox/index.ts` 能把 bash 整体换掉 [5][9]。文档里还有一条明确的能力边界声明："扩展运行在 Pi 进程内，拥有同样的操作系统权限。它可以检视 prompts、tool calls、文件、凭据与会话历史。" [5]

### 2.4 配置与"项目信任"的真实语义

两级配置：用户级 `~/.pi/agent/{settings,keybindings,models,auth}.json` 与 `SYSTEM.md`/`APPEND_SYSTEM.md`，加上 `extensions|skills|prompts|themes/` 目录；项目级 `.pi/` 镜像同一结构，由 **project trust** 门控 [6]。工具选择面是 `defaultTools` 加四个开关：`-t/--tools`、`-xt/--exclude-tools`、`-nbt/--no-builtin-tools`、`-nt/--no-tools`，其中"空数组会禁用全部内置工具，但不会禁用扩展与 SDK 工具" [6]。分发单位是能力包：`pi install npm:… | git:… | ./local` 一次带上扩展+技能+提示词+主题，且要求宿主包写在 `peerDependencies: "*"` 以避免重复注册表 [6]。

**"项目信任"不是权限系统**——这条极易误读，`security.md` 自己写得清楚："Project trust does not limit what tool calls can access or affect." [8]

### 2.5 上下文与会话：一棵 JSONL 树，不是一条列表

会话是 JSONL（v3），条目形如 `{type, id, parentId, timestamp}`，靠 `id`/`parentId` **在同一文件内原地分叉**；"截止到当前条目的那条分支就是活跃分支，为下一次模型请求提供历史" [2][7]。上下文是**类型化的** `AgentMessage`，经 `transformContext()` → `convertToLlm()` 才在 LLM 调用边界转成供应商消息 [3]。

压缩（compaction）确实存在（这点纠正了 2025-11 博客给人的印象）：触发式 `contextTokens > contextWindow - reserveTokens`，默认 `reserveTokens` 16384、`keepRecentTokens` 20000；机制是**追加一个 `CompactionEntry{summary, firstKeptEntryId}`**，原始条目留在树里但不再参与后续模型请求 [7]。重复压缩会从上一个 `firstKeptEntryId` 重新起算；`details` 是自由 JSON，因此扩展可以自定义自己的摘要格式 [7]。

最巧妙的一条设计是**状态归属**：扩展状态若应"跟着分支走"，就存进 tool-result 的 `details`；若是"不该进模型上下文的持久数据"，就用 `pi.appendEntry()` [5]。示例 `todo.ts` 正是靠这个做到"分支后 todo 状态自动对应该历史点" [9]。记忆机制是文本文件（`AGENTS.md` / `CLAUDE.md` / `SYSTEM.md`）的发现与加载，**没有记忆子系统、没有 RAG/嵌入** [6][8]。

---

## 3. pi 的拒绝清单：每一刀的原话、理由与替代物 ［置信度：High］

内置工具全集是 `read, bash, powershell, edit, write, grep, find, ls`，默认只开 `read/bash/edit/write` 四个；作者的说法是"这四个工具就是一个有效编码 agent 所需的一切"，且整个系统提示加工具定义**控制在 1000 token 以内**，理由是"前沿模型已经被 RL 训练到透了" [10]。

| 被拒能力 | 原话（作者/文档） | 理由类型 | 替代物 | example 交付？ |
|---|---|---|---|---|
| 权限/审批层 | "Pi does not include a built-in permission system for restricting filesystem, process, network, or credential access." [1]；"it does not ask for approval before every tool call." [8]；"pi runs in full YOLO mode and assumes you know what you're doing" [10] | 认为该控制无效："as soon as your agent can write code and run code, it's pretty much game over"，用 Haiku 预检 bash 是"mostly security theater" | OS/容器边界：Gondolin 微 VM、Plain Docker、Docker Sandboxes；`@anthropic-ai/sandbox-runtime` | 是：`permission-gate.ts`、`protected-paths.ts`、`confirm-destructive.ts`、`sandbox/`、`gondolin/` [8][9][10] |
| 子代理 | "You have zero visibility into what that sub-agent does. It's a black box within a black box."；并行实现是"an anti-pattern"；"Using a sub-agent mid-session for context gathering is a sign you didn't plan ahead." [10] | 可见性与上下文转移 | 起独立 `pi` 子进程 + frontmatter 声明工具白名单 | 是：`examples/extensions/subagent/`（`scout/planner/reviewer/worker`）[9] |
| MCP | "pi does not and will not support MCP… MCP servers are overkill for most use cases, and they come with significant context overhead"——"That's 7-9% of your context window gone before you even start working" [10] | token 经济 | 自建 CLI 工具 + 按需读的 README（渐进披露） | 否（`docs/` 里 "MCP" 零出现）[10] |
| 内置 todo | "pi does not and will not support built-in to-dos… to-do lists generally confuse models more than they help. They add state that the model has to track and update." [10] | 模型认知负担 | 一个普通 `TODO.md` | 是：`todo.ts`（状态存 `details`）[9] |
| plan mode | "pi does not and will not have a built-in plan mode"——理由是"我需要规划的可观测性，而 Claude Code 的 plan mode 给不了"，且文件形态可跨会话共享、可版本化 [10] | 可观测性 | 文件式计划 | 是：`plan-mode/`（禁用内置写工具 + 只读 bash allowlist）[9] |
| max-steps | "I never found a use case for that, so why add it?" [10] | 无用的旋钮 | 无 | — |
| 后台 bash | "Use tmux instead" [10] | 已有更优工具 | tmux | — |

Ronacher 从外部补了两条 pi 自己没写的第一性论据：其一，MCP 工具集必须在会话开始时载入系统上下文，"所以想在运行中完整重载工具能力，几乎不可能不砸掉整个 cache" [11]；其二，他把这些留白定性为立场而非欠工——"This is not a lazy omission. This is from the philosophy of how Pi works."，并给出 pi 的扩展范式："要让 agent 做它现在还不会的事，你不是去下载一个扩展或技能，**你是让 agent 自己扩展自己**" [11]。他还确认 pi 是"他所知系统提示最短的 agent"，并把 pi 定位成"为 agent 构建 agent 的底座"（OpenClaw 等建在其上）[11]。

**作者自认的代价**（对第 6 节关键）：统一 LLM API"因漏抽象而不可能完美"、token/成本统计是 best-effort、**长会话靠个人自律**（他自陈能在单个会话塞进"几百次交换"）而非机制解决 [10]。

---

## 4. 设计空间：四极与我们的位置 ［置信度：High（Codex/dsh 部分 Medium）］

### 4.1 四极对照

| | 谁拥有 agent 循环 | 扩展缝形态 | 缝的数量 | 内置工具可否被替换 |
|---|---|---|---|---|
| **pi** | 自家 `while(true)`，可注入 6 个环节 [3] | TS 模块，`ExtensionAPI`：registerTool/Command/Shortcut/Flag/Provider + notify/transform 两类事件 [5] | 少而深（扩展 + skills + prompts + themes + providers）[6] | **可以**：`createXTool` + `*Operations`，示例整个换掉 bash [5][9] |
| **opencode** | 框架自己（"边界可配，循环归 OpenCode"）[24] | JS/TS 插件：`tool.execute.before/after`、`shell.env`、`experimental.session.compacting`、通用 `event` [24][35] | **多而浅**：plugins / custom-tools / tools / agents / skills / commands / mcp-servers / lsp / formatters / permissions / policies / rules / themes / keybinds 等 15 个文档面 [36] | **只能改规格**：`tool.definition` 改 description/parameters；执行器不可按名替换（见 4.3）[36] |
| **Codex CLI** | 单个 `core` crate（`run_turn` 内外双层 loop）[31] | **协议优先**：`app-server` 一族 7 个 crate 提供 JSON-RPC/SQ-EQ 面，供 IDE/SDK 客户端 [32] | 一个协议 + 数据形态的 skills/plugins/MCP/AGENTS.md [32] | 未证实（Rust 原生，扩展走 crate/协议） |
| **dsh（DeepSeek Harness）** | **循环本身就是插件**，可被替换 [27] | Cordis 插件框架：`ctx.*` 类型化能力缝；事件分 waterfall（必须调 `next()`）与 serial 两类 [27] | 约 20 个 `ctx.*` 键 + profile/bundle 分层 [27] | **可以，且是默认假设**："No privileged core to patch" [27] |

dsh 是 pi 的直接反极：它把模型适配器、工具注册表、会话日志、**agent 循环本身**全做成插件，启动时按 profile/bundle 顺序装配，注册可逆；状态经 append-only 会话日志事件溯源，并立了一条运行不变量"**模型能看见的必须已记录**" [27]。有意思的是它也发 `sdk-minimal` profile 并称之为"deliberate exception"——**两极在同一个产品里共存** [27]。

### 4.2 一条否定性发现：缝的数量没有行业共识

opencode 十几个配置面、dsh 约 20 个 `ctx.*`、Codex 一个协议、pi 少而深 [24][27][32][5]。**"扩展面该有几个"是品味决策，不是标准**。这对"我们的功能很乱"这个直觉很关键：乱不乱不由数量决定，而由"每个面能不能被独立替换、是否有文档、是否互相冲突"决定。

### 4.3 本轮核实推翻的两条流行说法

- **"Codex 是约 80 个 crate 的 Rust workspace"** —— 不成立。`codex-rs/Cargo.toml` 的 `[workspace] members` 列出 **153 个 crate 路径**，`codex-rs/` 下有 118 个子目录；wiki 少报约一半。另外 `guardian` **不是**顶层 crate，真实形态是 `guardian-context`、`ext/guardian-v2`、`ext/guardian-reviewer` 与 `core/src/guardian/` [30]。原始说法出自机器生成的仓库 wiki（Tier 3）[26]。
- **"opencode 的插件工具按名字覆盖内置工具"** —— 不成立。`packages/opencode/src/tool/registry.ts` 的 `State = { custom, builtin, … }`，`all()` 返回 `[...s.builtin, ...s.custom]`，**没有按 id 去重或替换**；插件能覆盖的是内置工具的**规格**（对每个可见工具 `plugin.trigger("tool.definition", …)` 改写 description/parameters）[36]。这条原本来自官方文档的表述 [24]，被代码推翻。

同时被**证实**的：Effect-TS 是 opencode 的第一方运行时（`packages/core/package.json` 依赖 `effect` 及 `@effect/*`）[33]；`packages/core/src/permission.ts` 首行即 `export * as PermissionV2 from "./permission"`，用 `Context.Service("@opencode/v2/Permission")` + `Layer.effect` + `Deferred`，并用 `addFinalizer` 把挂起的 ask 判为 `DeclinedError`；事件名 `permission.v2.asked` / `permission.v2.replied`，effect 字面量 `"allow" | "deny" | "ask"`；**"v2" 不是版本号**（包版本 1.18.32），而是仓内命名空间——`packages/plugin/src/v2/effect/` 与 `core/src/v1/`（含 `v1/config/*` 与 `migrate.ts`）并存，用户可见配置仍是 v1 形状 [34][35]。

### 4.4 我们落在哪

按代码取证：我们**在 harness 层做厚实现，但只开了少数几条缝**，且这些缝几乎都只到"加工具"为止。

| 缝 | 能加什么 | 改不了什么 |
|---|---|---|
| `@register`（`registry.py:40`） | 一个 `BaseTool` 类 | 拓扑、状态 schema、确认语义；内置工具仍需在 `core/tools/__init__.py` 加 side-effect import |
| `plugins_dir` + `discover_plugins`（`registry.py:86`） | **零核心改动**加工具——最接近 pi 的那条缝 | 只能加工具；加不了节点/边/事件 |
| MCP 配置 / OpenAPI spec | 纯配置扩工具集 | 命名规则与写类启发式 |
| `git_enabled` | 只有开关 | 7 个 git 工具硬编码在 `build_git_tools`，第 8 个动词要改两个核心文件 |
| skills 目录 | 纯配置扩能力 | 只在两个声明根读取；无命令/钩子注册 |
| 77 个 settings 键 | 各子系统开关、预算、阈值 | **任何结构性分支**都硬编码在 `nodes.py` / `graph.py` |

对照 dsh 的"No privileged core to patch" [27] 与 pi 的"内置工具不特权" [5]：**我们有一条真缝（plugins_dir），但它只通到工具层**；其余能力面都是"改核心"。这是 B 类差距的根源。

---

## 5. 差距分析：逐条 harness 职责对照 ［置信度：High］

职责清单取自 [13][14][41] 的并集，逐条给"pi 怎么做 / 我们怎么做 / 差距类型"。

| harness 职责 | pi | langgraph-agent | 类型 |
|---|---|---|---|
| **循环与终止** | 手写 `while(true)`，无 max-steps，靠排队消息与工具续跑 [3][10] | StateGraph + 9 个手写路由 + `max_steps=15` + `recursion_limit`（代码取证） | C（主动更厚） |
| **上下文构成** | 类型化 `AgentMessage` → `transformContext` → `convertToLlm`，只在 LLM 边界转换 [3] | 线性 `messages` 列表 + 两层注入（`inject.py`）+ 提示词模板（`prompts.py`） | B（可替换性差） |
| **压缩** | 插入式 `CompactionEntry`，原文留在树里；`details` 允许扩展自定义摘要格式 [7] | `context.py` 截断/搬大件 + `context_token_budget=32000`；已知不收敛缺陷（#49） | B（且实现有已知 bug） |
| **会话与分支** | JSONL 树 `id`/`parentId`，`/tree` 原地分叉；扩展状态跟分支 [2][7] | SQLite checkpointer，`thread_id == task_id`；无用户可见的分支/时间旅行面 | A + B（LangGraph 本身支持 time travel [20]，我们没做成产品面） |
| **记忆** | 无子系统；`AGENTS.md`/`CLAUDE.md` 文本发现 [6][8] | KB（关键词，嵌入是占位）+ `memory_search` 工具 | C |
| **工具面** | 默认 4 个，全集 8 个，可整类禁用；prompt+工具 <1000 token [6][10] | 11 个 `@register` 内置 + 7 个 git + MCP/OpenAPI 动态追加（代码取证） | C |
| **工具扩展** | `registerTool` + 可替换 `*Operations`，热重载、同进程 [5] | `@register` / `plugins_dir`（只到工具层） | B |
| **权限与确认** | **无内建**；边界=容器；`tool_call` 钩子可 block，且钩子异常 fail-safe 拦下 [1][5][8] | 图内 `human_confirm` 节点 + 手写 `threading.Event` 轮询；MCP 逐调用启发式判定；**LangGraph 原生 `interrupt()` 零使用**（代码取证） | C（且 #55 一族缺口正在处理） |
| **子代理** | 无内建，示例扩展起独立子进程 + frontmatter 白名单 [9][10] | 内建两条入口（工具 + 图节点），同进程线程池，继承父全量工具面 | C（差距见 #55 取证） |
| **续跑/持久化** | JSONL 即事实来源；`pi-durable` 兄弟包 [1][4] | LangGraph SqliteSaver + durability `"sync"` + 格式戳（代码取证）——**这正是 [40] 列的框架交换价值** | A（框架带来的收益） |
| **可观测** | 事件流 + JSON/RPC 模式；transcript 全透明（"Pi treats you like an adult" [28]） | EventBus + trace 文件 + SSE + 前端工作台 | A/C |
| **多客户端/鉴权** | 无（单终端） | FastAPI + bearer/TTL（默认关） | A |
| **回滚** | 无（靠 git 与工作区自律） | before-image 账本 + `POST /tasks/{id}/rollback`（只覆盖 `file_io`/`edit` 两处写） | C（但覆盖不全，见 #55 追加） |
| **评测** | 有 `pi-evals` 包（本轮未展开） | `live_e2e.py` / `live_skill_test.py` / headless 四级退出码 | A/C |
| **熔断与重试** | 无内建 | `resilience.py`（CI 冻结区） | C |

### 5.1 差距分布

15 条职责里：**A 类（场景差异）4 条**、**C 类（主动更厚）6 条**、**B 类（缺缝造成的债）5 条**。B 类集中在同一件事上：**上下文构成、压缩、会话分支、工具扩展、副作用登记**——也就是"模型看见什么、改完能不能收回"这两族能力。

### 5.2 最刺眼的一条

不是"我们功能太多"，而是**功能与缝不成比例**。pi 用 8 个内置工具 + 一条扩展 API 覆盖了注册工具/命令/快捷键/CLI flag/provider 五种扩展意图 [5][6]；我们有 18 到 20 多个工具、20 项运行时能力，但**只有 `plugins_dir` 一条不需要改核心的缝，且它只通到工具**（代码取证）。具体后果：加第 8 个 git 动词要改 `git_tools.py` + `task_manager.py`；加一种事件要改 `nodes.py`；加一个状态字段要改 `state.py` 并考虑 checkpoint 格式兼容。

### 5.3 pi 有而我们没有的

四条，都不是"功能"而是"机制"：**原地分叉的会话树**（我们的续跑是线性恢复）[2][7]；**扩展状态跟分支走**（`details` 语义）[5]；**内置工具可被整体替换**（`*Operations`）[5]；**对 prompt cache 的显式意识**（工具集在会话开始烘进系统上下文，故拒绝热重载 MCP）[11]。最后一条我们既没有机制也没有文档表述。

### 5.4 我们更厚、且 pi 用脚投票不做的

checkpoint 续跑、SSE 多客户端、鉴权、回滚账本、熔断/重试、真实模型 E2E、风险扫描、完成校验、技能运行时、两层注入。这些里**只有"完成校验"和"两层注入"在 pi 的拒绝清单里有直接对应批评**（它认为模型自律 + 文件即可）；其余属于 A 类场景差异，不构成差距。

---

## 6. 极简路线的代价与批评轴 ［置信度：Medium］

### 6.1 已记录的四条代价

(a) **无权限层是明说的非目标**：默认全 YOLO [1][8][10]。(b) **无子代理/无编排**：作者视为纪律，部分实践者读作"不会规划" [10][28]。(c) **长任务失败无缓解**：作者自陈靠自律跑几百轮 [10]，社区有"它没干完活"的质量抱怨 [28]。(d) **拒绝 MCP 的生态代价**：换来 7-9% 上下文节省，也放弃了工具可发现性 [10][11]。

### 6.2 最强"支持极简"论据

来自最有动机推广框架的厂商之外：**Anthropic 官方**写"始终如此，最成功的实现用的是简单、可组合的模式，而非复杂框架"，框架"常常造出多余的抽象层，把底层 prompt 和 response 遮住，导致更难调试"，并建议"直接从 LLM API 开始：很多模式几行代码就能实现" [21]。这与 pi 的立场同构（"Exactly controlling what goes into the model's context yields better outputs" [10]），Ronacher 更进一步说现有 harness"其实都没真正让你做上下文工程" [11]。

### 6.3 最强"反对极简"论据

Willison 的 lethal trifecta：私有数据 + 不可信内容 + 对外通信同处一个会话时，任何能控制被读内容的一方都能指使 agent 外泄；因为所有输入塌缩成同一条 token 流，这是**架构性**风险，不是 prompt 能修的 [29]。这条同时**支持**"YOLO 危险"和**拆台**"审批按钮有用"——人点"同意"并不恢复来源隔离。Zechner 的"it's pretty much game over"是同一诊断、相反结论 [10]。社区侧还有一条更朴素的："如果 agent 想干什么就干什么，那它就不在 harness 里" [28]。

### 6.4 关键否定性发现：没有实测

**不存在任何受控比较测量过"编排框架 vs 裸循环"在长任务上的胜负。** Chroma 的 context-rot 是 18 模型、6 实验、194,480 次调用的真研究，但它操纵的变量是**输入长度**：聚焦提示（均值约 300 token）显著优于全量提示（约 113k token），且"哪怕一个干扰项都会让表现低于基线"，加四个干扰项进一步恶化 [37]。这与"编排方式"无关。Anthropic 说专职子代理"在复杂研究任务上比单 agent 系统有实质提升"，是**第一方断言、无公开对照** [38]。METR 的时间视野给出长任务量化图景（p50 从 Opus 4.5 的约 293 小时到 Claude Mythos Preview 的约 1045 小时，2023 年以来倍增周期约 128.7 天），但页面自己标注"**16 小时以上的测量在我们当前任务集下不可靠**"，且该指标不控制脚手架差异 [39]。

→ 因此"我们这层厚编排值不值"目前**无法用基准裁决**，它是设计姿态选择。

### 6.5 两家其实在说错话

Anthropic 反对的是"抽象遮住 prompt、难调试" [21]；LangChain 回答的是"执行持久化、确定性、checkpoint、HITL" [40][41]——**不同的失效轴**。真正的重叠只有一处：**两边都说要从最简开始**。Campos 的原话是"如果你的 agent 不需要这些特性（比如它是个没有工具、单条提示的短 agent），那你可能不需要 LangGraph，也不需要任何框架" [40]；1.0 发布文自认"LangChain 的抽象有时过于厚重，包面积已失控"，并把 legacy 挪进 `langchain-classic` [17]。至于"抽象遮蔽 prompt"这条具体指控，[42] 的中枢论述**并未正面回应**，它谈的是把循环各阶段暴露出来（"The core of every agent harness is the same, and remarkably simple: an LLM, running in a loop, calling tools"）[42][41]。

---

## 7. 未决问题（读完再定，不含方案）

1. **B 类五条差距要不要合并处理？** 上下文构成、压缩、会话分支、工具扩展、副作用登记在 pi 那里其实是**同一件事**：会话树 + 类型化条目 + `details` 跟分支 [2][5][7]。我们这边是五个独立模块。是否合并是架构判断，不是本报告结论。
2. **prompt cache 在我们这里值多少？** pi 拒绝 MCP 热重载的第一性理由是 cache [11]。我们每轮重拼 system prompt + 全量工具 schema，**没有测量过缓存命中率**，因此无法判断这条约束对我们是硬是软。
3. **"厚"与评测分的因果**：本地没有任何量化记录说明 20 项能力换来多少分数；`live_e2e` 只有两个场景且都不触发子任务（#55 取证）。
4. **`interrupt()` 未用是选择还是遗漏**：`graph.py:6` 的注释写着 "the `human_confirm` interrupt"，但代码是手写 `threading.Event` 轮询（代码取证）。是当年有意绕开，还是遗留措辞，未在任何 ADR/spec 中找到说明。
5. **pi 的维护面与生态健康度未查**：`pi-evals` 包内容本轮未展开；star 数、issue 响应、bus factor 均未取证。
6. **学术侧证据薄弱**：本轮只拿到 Chroma 与 METR 两份工程侧测量，无同行评审文献支撑长任务结论。
7. **一处仍未解决的口径冲突**：`[13]` 把 `TodoListMiddleware → write_todos` 列为 harness 标配职责，但 deepagents 当前 README 已不列独立 todo 工具 [16]。引用时需以 [19][42] 的中间件清单为准。

---

## 方法论

- **深度**：deep。检索子代理 4 个（pi 骨架与功能面 / harness 概念与 LangGraph 分工 / 参照点与批评轴 / 本仓库取证），补口子代理 2 个（Codex+opencode 仓库级核实 / context-rot+框架之争），引用核对子代理 1 个。共两轮检索波次。
- **来源**：去重后 42 条（Zechner 博客、Anthropic《Building Effective Agents》、LangChain 1.0 发布文各被两个代理重复命中，已合并）。Tier 1 占 30 条，Tier 2 占 8 条，Tier 3 占 4 条（HN 讨论、机器生成 wiki）。
- **引用抽查结果**：8 条高影响断言中 7 条 SUPPORTED、1 条 PARTIAL。据此做了四处修正：(i) C4 从"每项被拒能力都有 example"收窄为"权限/安全这一类有 example，且它只是 79 项 example 中的一类"；(ii) `pi-ai` 依赖描述补上 `partial-json`、proxy-agent 与 `pi-telemetry`；(iii) compaction 的版本归属改为"由 manifest 佐证，文档本身不带版本号"；(iv) `building-langgraph` 署名改为单人作者 Nuno Campos。
- **大纲调整**（Phase 3.5，改动 <50%）：默认四段结构（现状/趋势/批判/行动）不适配"只出差距不出方案"的用户约束，改为 §1 术语 → §2-3 pi → §4 设计空间 → §5 差距 → §6 批评轴 → §7 未决。**Action Plan 一节被刻意替换为"未决问题"**，因为用户明确要求暂不定修改方案。
- **本仓库侧证据**：全部来自 `master @ 4857f03` 的代码取证（file:line 形式内联），不走网络来源，故不入参考文献编号。
- **已知偏差**：pi 侧材料以第一方文档与作者博客为主（虽 Tier 1，但立场自陈）；批评轴主要靠 HN（Tier 3）与二手引述，社区情绪 ≠ 技术事实；Codex 架构只到 crate/文件级，未读实现细节；dsh 与 opencode 的中文社区视角缺失。
- **未证实即标注**：[22][23] 两条为检索命中但未取全文，正文中未据其立论；opencode "v2 = Effect 重写"已由 [33][34][35] 一手确认，早期"文档不承认"的保守判断作废。

---

## 参考文献

**pi（一手仓库，v0.87.1 / main@8676a0dc）**

[1] earendil-works — pi 根 README — https://github.com/earendil-works/pi/blob/main/README.md — 2026-09-24 — Tier 1
[2] pi maintainers — packages/coding-agent/docs/how-pi-works.md — https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/how-pi-works.md — Tier 1
[3] pi maintainers — packages/agent/README.md + src/agent-loop.ts — https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent-loop.ts — Tier 1
[4] pi maintainers — packages/* package.json（v0.87.1 依赖图）— https://github.com/earendil-works/pi/tree/v0.87.1/packages — Tier 1
[5] pi maintainers — docs/extensions.md — https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md — Tier 1
[6] pi maintainers — configuration.md / settings.md / cli.md / packages.md — https://github.com/earendil-works/pi/tree/main/packages/coding-agent/docs — Tier 1
[7] pi maintainers — session-format.md / compaction.md / message-types.md — https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md — Tier 1
[8] pi maintainers — security.md / containerization.md — https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md — Tier 1
[9] pi maintainers — examples/extensions（README、subagent/、permission-gate.ts、sandbox/、plan-mode/、todo.ts）— https://github.com/earendil-works/pi/tree/main/packages/coding-agent/examples/extensions — Tier 1
[10] Mario Zechner — What I learned building an opinionated and minimal coding agent — https://mariozechner.at/posts/2025-11-30-pi-coding-agent/ — 2025-11-30 — Tier 2（设计意图的一手来源；比 v0.87.1 早约 10 个月，与仓库文档冲突处以文档为准）
[11] Armin Ronacher — Pi: The Minimal Agent Within OpenClaw — https://lucumr.pocoo.org/2026/1/31/pi/ — 2026-01-31 — Tier 2
[12] pi maintainers — AGENTS.md — https://github.com/earendil-works/pi/blob/main/AGENTS.md — Tier 1

**harness 概念与 LangGraph/LangChain 分工**

[13] Datawhale（沧海九粟）— Deep Agents 实战 Ch.1 "From Agent Framework to Agent Harness" — https://datawhalechina.github.io/deepagents-in-action/chapters/ch01-agent-harness — Tier 1（本报告锚定教材）
[14] Vivek Trivedy — The Anatomy of an Agent Harness — https://www.langchain.com/blog/the-anatomy-of-an-agent-harness — 2026-03-10（mod 2026-05-21）— Tier 1
[15] Vivek Trivedy — Improving Deep Agents with harness engineering — https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering — 2026-02-17 — Tier 1
[16] langchain-ai — deepagents README — https://github.com/langchain-ai/deepagents — Tier 1
[17] LangChain — LangChain and LangGraph Agent Frameworks Reach v1.0 Milestones — https://www.langchain.com/blog/langchain-langgraph-1dot0 — 2025-10-22 — Tier 1
[18] LangChain Docs — What's new in LangGraph v1 — https://docs.langchain.com/oss/python/releases/langgraph-v1 — Tier 1
[19] LangChain Docs — Middleware overview — https://docs.langchain.com/oss/python/langchain/middleware — Tier 1
[20] LangChain Docs — LangGraph persistence — https://docs.langchain.com/oss/python/langgraph/persistence — Tier 1
[21] Anthropic — Building Effective Agents — https://www.anthropic.com/engineering/building-effective-agents — 2024-12-19 — Tier 1
[22] Anthropic — Harness design for long-running application development — https://www.anthropic.com/engineering/harness-design-long-running-apps — 2026-03-24 — Tier 1（检索命中，未取全文，正文未据其立论）
[23] HumanLayer — 12-factor-agents + "Skill Issue: Harness Engineering for Coding Agents" — https://github.com/humanlayer/12-factor-agents / https://www.humanlayer.dev/blog/skill-issue-harness-engineering-for-coding-agents — 2026-03-12 — Tier 2（同上，未取全文）

**参照点与批评轴**

[24] OpenCode (anomalyco) — Docs: Plugins — https://opencode.ai/docs/plugins/ — Tier 1
[25] openai/codex — 仓库根 README — https://github.com/openai/codex — Tier 1（架构信息缺失本身即发现）
[26] Zread — openai/codex "Hooks engine" — https://zread.ai/openai/codex/17-hooks-engine — Tier 3（机器生成；其 crate 数与 `guardian` 命名已被 [30] 推翻）
[27] DeepSeek — deepseek-harness/docs/architecture.md — https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md — Tier 1
[28] Hacker News — "Pi – A minimal terminal coding harness" 讨论 — https://news.ycombinator.com/item?id=47143754 — Tier 3（社区情绪证据）
[29] Simon Willison — The lethal trifecta for AI agents — https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ — 2025-06-16 — Tier 2

**仓库级核实（Codex / opencode）**

[30] openai/codex — codex-rs 目录 + Cargo.toml workspace members — https://api.github.com/repos/openai/codex/contents/codex-rs — Tier 1
[31] openai/codex — core/src/session/turn.rs、core/src/tools/router.rs、core/src/thread_manager.rs、core/src/tools/multi_agent_tool.rs — https://api.github.com/repos/openai/codex/contents/codex-rs/core/src — Tier 1
[32] openai/codex — app-server/README.md、skills/、plugin/、docs/protocol_v1.md — https://api.github.com/repos/openai/codex/contents/codex-rs/app-server — Tier 1
[33] anomalyco/opencode — packages/{core,plugin,opencode,protocol}/package.json（dev 分支，v1.18.32）— https://api.github.com/repos/anomalyco/opencode/contents/packages — Tier 1
[34] anomalyco/opencode — packages/core/src/permission.ts、core/src/v1/*、schema/src/permission.ts — https://api.github.com/repos/anomalyco/opencode/contents/packages/core/src/permission.ts — Tier 1
[35] anomalyco/opencode — packages/plugin/src/index.ts、v2/effect/PLAN.md、v2/effect/README.md — https://api.github.com/repos/anomalyco/opencode/contents/packages/plugin/src — Tier 1
[36] anomalyco/opencode — packages/opencode/src/tool/registry.ts + docs 清单 — https://api.github.com/repos/anomalyco/opencode/git/trees/dev — Tier 1

**长任务与框架之争**

[37] Chroma（Kelly Hong, Anton Troynikov, Jeff Huber）— Context Rot: How Increasing Input Tokens Impacts LLM Performance — https://www.trychroma.com/research/context-rot — 2025-07-14 — Tier 1
[38] Anthropic — Effective context engineering for AI agents — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — 2025-09-29 — Tier 1
[39] METR — Task-Completion Time Horizons of Frontier AI Models（TH 1.1）— https://metr.org/time-horizons/ — Tier 1
[40] Nuno Campos — Building LangGraph: Designing an Agent Runtime from first principles — https://www.langchain.com/blog/building-langgraph — 2025-09-04 — Tier 1
[41] LangChain — Deep Agents vs LangChain vs LangGraph — https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph — 2026-08-06 — Tier 1
[42] LangChain — How Middleware Lets You Customize Your Agent Harness — https://www.langchain.com/blog/how-middleware-lets-you-customize-your-agent-harness — 日期未标 — Tier 1

---

## 来源摘录（关键条目）

### [1] pi 根 README
- 摘要：仓库定位为 "AI agent toolkit: unified LLM API, agent loop, TUI, coding agent CLI"；七个公开包；供应链硬化（精确版本、`save-exact`、`min-release-age=2`、shrinkwrap、脚本 allowlist）。
- 关键引文："Pi does not include a built-in permission system for restricting filesystem, process, network, or credential access." / "By default, it runs with the permissions of the user and process that launched it."
- 类型：官方文档 ｜ Tier 1

### [3] agent-loop.ts
- 摘要：显式 `while (true)` + 六个可注入环节；外层处理"该停时又来排队消息"，内层 `while (hasMoreToolCalls || pendingMessages.length > 0)`；事件流命名完整；工具执行 `parallel`（默认）/`sequential`；SQLite 后端刻意拆包以免核心带原生依赖。
- 关键引文：文件注释 "Outer loop: continues when queued follow-up messages arrive after agent would stop"；核对确认该文件仅 import `@earendil-works/pi-ai` 与两个本地模块。
- 类型：源码 ｜ Tier 1

### [5] extensions.md
- 摘要：`ExtensionAPI` 全表面；事件分 notify 与 transform/cancel；状态存储决策表按"状态如何参与对话"划分；模式降级 interactive > RPC > JSON/print。
- 关键引文："`tool_call` can mutate input or block execution." / "A `tool_call` handler failure blocks the tool as a fail-safe." / "An extension runs inside the Pi process with the same operating-system permissions." / "Tool state that follows the active branch → Tool-result `details`."
- 类型：官方文档 ｜ Tier 1

### [7] compaction.md
- 摘要：触发式压缩与分支摘要两种机制；重复压缩从上一个 `firstKeptEntryId` 重启；`details` 自由 JSON。
- 关键引文：`contextTokens > contextWindow - reserveTokens`（默认 16384 / keepRecentTokens 20000）；"Compaction inserts a summary entry that replaces older messages in subsequent model requests. The original entries remain in the session tree."
- 类型：官方文档 ｜ Tier 1

### [8] security.md / containerization.md
- 摘要：安全边界被定义为 OS/虚拟化层，其余全部明确否认；项目信任只管资源加载。
- 关键引文："it does not ask for approval before every tool call." / "Project trust does not limit what tool calls can access or affect." / "Watching the transcript, using project trust, and reviewing changes do not create a security boundary."
- 类型：官方文档 ｜ Tier 1

### [9] examples/extensions
- 摘要：79 项示例（9 目录 + 70 文件），README 把 `permission-gate.ts`、`project-trust.ts`、`protected-paths.ts`、`confirm-destructive.ts`、`dirty-repo-guard.ts`、`sandbox/`、`gondolin/` 归入 "Lifecycle & Safety" 一类；同目录另有 `snake.ts`、`tic-tac-toe.ts`、`doom-overlay/` 等玩具与 provider 示例。
- 关键引文（sandbox 示例自述）："This example intentionally overrides the built-in `bash` tool to show how built-in tools can be replaced."；`todo.ts`："State is stored in tool result details (not external files), which allows proper branching."
- 类型：源码/示例 ｜ Tier 1

### [10] Zechner 博客
- 摘要：极简路线的创始文本；每项拒绝配机制理由；自认代价（漏抽象、best-effort 计量、无压缩依赖自律）。
- 关键引文："if I don't need it, it won't be built. And I don't need a lot of things." / "pi runs in full YOLO mode and assumes you know what you're doing." / "You have zero visibility into what that sub-agent does. It's a black box within a black box." / "That's 7-9% of your context window gone before you even start working." / "Exactly controlling what goes into the model's context yields better outputs."
- 类型：一手设计意图（个人博客）｜ Tier 2

### [11] Ronacher
- 摘要：外部独立佐证；给出 MCP 的 prompt-cache 论据；把 pi 定位为构建 agent 的底座。
- 关键引文："This is not a lazy omission. This is from the philosophy of how Pi works." / "you don't go and download an extension… You ask the agent to extend itself." / MCP 工具 "need to be loaded into the system context… on session start. That makes it very hard or impossible to fully reload… without trashing the complete cache."
- 类型：实践者长文 ｜ Tier 2

### [13] Datawhale ch01
- 摘要：三层模型（Runtime/Framework/Harness）与车间比喻；四项预设 harness 能力；虚拟 FS 工具集列举。
- 关键引文："An 'out-of-the-box' Agent suite that pre-installs a verified set of tool interfaces and middleware frameworks on top of Runtime and Framework."
- 类型：课程教材（本报告锚）｜ Tier 1 ｜ 缺口：全章未提 pi

### [14] Anatomy of an Agent Harness
- 摘要：框架作者侧最明确的 harness 成分表；二分定义。
- 关键引文："A harness is every piece of code, configuration, and execution logic that isn't the model itself." / "If you're not the model, you're the harness." / "The model contains the intelligence and the harness makes that intelligence useful."
- 类型：厂商工程博客 ｜ Tier 1

### [16] deepagents README
- 关键引文："LangGraph is the graph runtime. LangChain's `create_agent` is a minimal agent harness on top of it. Deep Agents is a more opinionated harness on top of `create_agent` — same building blocks, but with filesystem, sub-agents, context management, and skills bundled in." / "Enforce boundaries at the tool/sandbox level, not by expecting the model to self-police."
- 类型：官方仓库 ｜ Tier 1

### [21] Anthropic Building Effective Agents
- 关键引文："Consistently, the most successful implementations use simple, composable patterns rather than complex frameworks." / "they often create extra layers of abstraction that can obscure the underlying prompts and responses… making them harder to debug." / "many patterns can be implemented in a few lines of code."
- 类型：厂商工程指南（2024，属"foundational"）｜ Tier 1

### [27] dsh architecture.md
- 关键引文："No privileged core to patch — you extend dsh by mounting a plugin beside others." / 事件分 waterfall（"listeners must call `next()`"）与 serial / 不变量 "Model-visible means logged" / 提供 `sdk-minimal` profile 作为 "deliberate exception"。
- 类型：官方文档 ｜ Tier 1

### [29] lethal trifecta
- 关键引文：三要素 "Access to your private data" / "Exposure to untrusted content" / "The ability to externally communicate"；根因 "LLMs are unable to reliably distinguish the importance of instructions based on where they came from"；对护栏厂商 "in web application security 95% is very much a failing grade."
- 类型：权威实践者 ｜ Tier 2

### [30][31][32] Codex 仓库级事实
- 118 个子目录、**153 个 workspace member 路径**；`run_turn`（turn.rs:163）内外双层 loop；`ToolRouter`（router.rs:74）；`ThreadManager`（thread_manager.rs:232，含 `list_agent_subtree_thread_ids`）；`multi_agent_tool.rs` 自述 "Tools for spawning and managing sub-agents."；app-server 一族 7 crate，JSON-RPC + SQ/EQ 协议，且 `protocol_v1.md` 自注 "The code might not completely match this spec."
- 类型：源码/清单 ｜ Tier 1

### [34][35][36] opencode 仓库级事实
- `effect` 为第一方依赖；`PermissionV2` 用 `Context.Service("@opencode/v2/Permission")` + `Deferred` + `addFinalizer`（挂起 ask 判 `DeclinedError`）；事件 `permission.v2.asked`/`replied`；v1 配置树与 v2 运行时并存；`registry.ts` 的 `all()` 返回 `[...builtin, ...custom]` **无按名替换**，插件仅经 `tool.definition` 改规格。
- 类型：源码 ｜ Tier 1

### [37] Chroma Context Rot
- 摘要：18 模型 / 6 实验 / 194,480 次调用；变量为输入长度、needle 位置、干扰项数、haystack 结构与相似度；judge 与人工一致率 >99%。
- 关键引文："Even on tasks as simple as non-lexical retrieval or text replication, we see increasing non-uniformity in performance as input length grows." / "Even a single distractor reduces performance relative to baseline." / 聚焦（~300 token）显著优于全量（~113k token）。
- 类型：受控研究（工程侧）｜ Tier 1

### [40] Building LangGraph
- 关键引文："Agents too can be written directly as a single function with one big while loop." / "But when you do that, you lose the ability to implement features like checkpointing or human-in-the-loop." / "The biggest competitor to any code framework is always no framework." / "If your agent fails on minute 9 of 10, going back to the beginning is pretty time consuming and also expensive." / 让步："We prioritized production-readiness over how easy it would be for people to get started."
- 类型：厂商工程博客（署名 Nuno Campos）｜ Tier 1

### [41] Deep Agents vs LangChain vs LangGraph
- 关键引文："LangGraph is an agent runtime, LangChain is an agent framework, and Deep Agents is an agent harness." / "The runtime offers the most control and the least abstraction; the harness offers the inverse." / harness 的职责是 "to get the right context to the model at the right time via context engineering." / "All three are fully composable, so you can move between layers instead of picking one."
- 类型：厂商工程博客 ｜ Tier 1
