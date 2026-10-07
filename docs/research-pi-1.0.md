# pi 1.0.x 调研：发布要点与对 langgraph-agent 的启发

> 生成 2026-10-07 ｜ 证据基准：tag **v1.0.4**（commit `7c10bd4337495ee613f2224843ecdf349b80d1df`，tag 发布 2026-10-05T22:03:50Z，commit 日期 2026-10-05 21:22:49 +0200）；上一轮基线 **v0.87.1**（2026-09-22T19:43:43Z）｜ 方法：GitHub Releases/Tags/源码/文档一手源 + 编号引用

**取证锚（不臆测版本号）**：`gh api repos/earendil-works/pi/releases` 返回最近 10 个 release 依次为 v1.0.4 / v1.0.3 / v1.0.2 / v1.0.1 / v1.0.0 / v0.99.2 / v0.99.1 / v0.99.0 / v0.87.1 / v0.87.0；`tags` 前 15 与之对齐 [31]。**1.0.x 真实存在**：v1.0.0 = 2026-10-01，最新 v1.0.4 = 2026-10-05。仓库默认分支 `main`，MIT，113k stars [33]。

> 本报告的「源码/发布物实证」与「推断」分开标注。凡一手源查不到的，写「未找到一手证据」，不补脑补。

---

## TL;DR

1. **1.0.0 的 release notes 里没有 Breaking Changes 段**；真正的 1.0 破坏性变更写在 `packages/agent/CHANGELOG.md`——把实验性 harness（sessions/compaction/skills/prompt templates/telemetry）从 `@earendil-works/pi-agent-core` 里**删掉**，该包只剩 `Agent` + agent loop + proxy stream [5][10]。包结构变化：v0.87.1 → v1.0.4 **删了 `session-backends`，新增 `codemode` / `env` / `mcp` 三个一等包** [34]。
2. **0.87.1 → 1.0.x 最大的能力增量是「扩展 API 的工具编排面」**：`exposure`（direct/model-only/codemode/deferred/hidden）、`namespace`、`annotations`、`outputSchema`+`structuredContent`、`prepareLoadout()`、`ctx.executeTool()`（嵌套调用 + 有界 `nestedCalls` 记录）、`registerVirtualModel()`、`registerMcpServer()`、`registerToolRenderer()`——这些在 v0.87.1 的 `extensions.md` 里**一次都没出现**（对 `exposure` 等关键词 0 命中，全文只有 1 处 `setActiveTools`），v1.0.4 有 17 处 [35][16]。这是对 #67「能力包可拆装」最直接的一手参照。
3. **pi 根本没有 planner/executor 两个节点**：全文文档检索 `planner` 命中 0 [36]。loop 只有一个 `agentLoop` [12]；**plan mode 是「同一个 agent 的模式切换」**，而且从 v0.87.1 到 v1.0.4 **字节级没变**（`plan-mode/index.ts` blob sha 两边同为 `737ce56ac7`），仍是 examples 扩展、**没有转正** [27][32]。
4. **子代理在 1.0 仍是那个「spawn 独立 pi 进程」的 example，同样字节级未变**（`71b1a33dc7`）[28][32]；它对「black box subagent」的回应不在 API 而在**渲染层**：子进程的 tool call、最终输出、每任务 usage/cost/stopReason 全部回填进父转录 [28]。
5. **pi 1.0 没有内置逐调用审批闸门**，`security.md` 明说「does not ask for approval before every tool call」；审批完全是扩展责任（`tool_call` 返回 `{block, reason}`）[18][29]。这与 langgraph-agent 自研确认闸门是**反面参照而非可借形态**。

---

## 1. 1.0 发布本体（版本、日期、breaking changes）［置信度：High］

### 1.1 版本与日期（一手：GitHub Releases）

| Tag | 发布时间 (UTC) | release notes 是否含 Breaking Changes 段 |
|---|---|---|
| v1.0.4 | 2026-10-05T22:03:50Z | 无 |
| v1.0.3 | 2026-10-05T08:38:23Z | **有**：Azure provider 从 `azure-openai-responses` 改名 `azure`，`auth.json`/`models.json`/`settings.json` 的 key 都要改；老 session 恢复时回退到别的模型且 **prompt cache 不复用** [2] |
| v1.0.2 | 2026-10-04T00:56:36Z | 无 |
| v1.0.1 | 2026-10-03T16:14:00Z | 无（但有 Removed：发布包删掉 `npm-shrinkwrap.json`，npm 装法不再钉间接依赖，要钉版得用 pi.dev installer）[4] |
| **v1.0.0** | **2026-10-01T19:20:55Z** | **无 Breaking Changes 段**（章节只有 New Features / Added / Changed / Fixed）[5] |

v1.0.0 的「Changed」里有两条是行为破坏而非破坏性 API：**默认 TUI 从 scrollback 改为 fullscreen**（`tuiMode: "regular"` 或 `--tui-mode regular` 可退回）[5]。

### 1.2 真正的 1.0 结构性破坏：`pi-agent-core` 被掏空［High］

`packages/agent/CHANGELOG.md` 的 `[1.0.0] - 2026-10-01` 有 `### Breaking Changes`，全文一段 [10]：

> Removed the experimental harness from `@earendil-works/pi-agent-core`: `AgentHarness`, sessions and session storage, the durable runtime, pico3, harness tools, compaction, skills, prompt templates, system prompt helpers, telemetry schemas, the search service types, and the `uuidv7` and pi-telemetry re-exports. The `./node`, `./harness/*`, and `./experimental/pico3` subpath exports are gone. The package now contains only `Agent`, the agent loop, the proxy stream, and their types. Use `@earendil-works/pi-durable` for durable sessions.

即：**1.0 的动作是「把纯 agent loop 与会话/压缩/技能解耦」**。`packages/agent/package.json` 的 exports 现在只有 `.` 和 `./package.json` 两个入口 [34]。这条对本仓库的直接意义见 §9。

### 1.3 包结构 diff（一手：v0.87.1 tree vs v1.0.4 tree）［High］

| | v0.87.1 (12 包) | v1.0.4 (14 包) |
|---|---|---|
| **删除** | `session-backends` | — |
| **新增** | — | `codemode`、`env`、`mcp` |
| 共有 | agent, ai, chord, client, coding-agent, durable, evals, protocol, server, telemetry, tui | 同左 |

`docs/` 页面对比：**只新增 `codemode.md` / `mcp.md` / `virtual-models.md`，零删除** [37]。源码侧对应 `packages/coding-agent/src/extensions/{codemode,llama,mcp,tool-search}` 四个 built-in 扩展 [38]。

### 1.4 「1.0 意味着什么」——稳定性承诺［置信度：Low / 未找到一手证据］

- 仓库内（README / CONTRIBUTING / AGENTS.md / docs/index.md / settings）**检索不到任何 semver 政策或「1.0 = API 稳定」声明** [39]。
- 维护者一手博客 `mariozechner.at` 最新一篇是 2026-05-30，**没有 1.0 发布公告**；因此「1.0 的稳定性承诺」无一手可引 [40]。
- 反向证据很有信息量：**`packages/durable/README.md` 开头明确写 `> **Experimental.** The API changes without notice between releases.`** [13]。也就是说 1.0 这个版本号**没有**覆盖到 durable 包的 API 稳定性。
- 可实证的「稳定」只有两条：Nix flake 提供 `github:earendil-works/pi/stable` 指向最新 release，release 归档带 `SHA256SUMS` [41]；直接外部依赖钉死精确版本 [42]。

---

## 2. Agent 循环与 plan/execute［置信度：High］

### 2.1 循环结构：一个 `agentLoop`，没有 planner/executor 分层

`how-pi-works.md` 的 Agent loop 一节 [15]：

> A submitted message is added to the active branch. Pi builds a model request from the system prompt, active branch, available tools, and model settings, then sends it through the selected provider. The provider streams an assistant response, which can contain text and tool calls. Pi records the response, executes each tool call, and records the results. That completes one turn. If tool results or queued messages require another model request, Pi starts another turn.

源码 `packages/agent/src/agent-loop.ts` 的 `runAgentLoop`：`declareToolChanges(context, prompts)` → `agent_start` → `turn_start` → 逐条 emit prompt 消息 → provider 请求 → 工具执行 → 下一轮 [12]。`packages/agent/README.md` 给出完整事件序列（`agent_start / turn_start / message_start|update|end / tool_execution_start|update|end / turn_end / agent_end`），并说明工具默认 `parallel` 执行、`beforeToolCall` 可 block、`terminate: true` 可在**一批工具结果全部同意时**提前结束自动跟进 [11]。

**「planner」在 pi 文档里不存在**：对 `packages/coding-agent/docs` 检索 `planner|planning agent|plan agent` → **0 命中** [36]。

### 2.2 plan mode：模式切换，不是独立 agent/节点［High］

`examples/extensions/plan-mode/index.ts` 的机制，逐条对应「模式切换」这个词 [27]：

| 机制 | 实现 |
|---|---|
| 工具面切换 | `pi.setActiveTools(getPlanModeTools(...))`：过滤掉 `edit`/`write`，加入 `read/bash/grep/find/ls/questionnaire`；退出时 `restoreNormalModeTools()` 还原 |
| 行为收窄 | `pi.on("tool_call")` 对 `bash` 做只读命令白名单，不满足返回 `{block: true, reason}` |
| 提示注入 | `pi.on("before_agent_start")` 返回一条 `customType: "plan-mode-context"` 的隐藏消息（`display: false`），内容是 `[PLAN MODE ACTIVE] ... Create a detailed numbered plan under a "Plan:" header` |
| 反向清理 | `pi.on("context")` 在非 plan 模式下**过滤掉**残留的 plan-mode 上下文消息，避免过期指令污染 |
| 状态持久化 | `pi.appendEntry("plan-mode", {...})` 存 `enabled/todos/executing/toolsBeforePlanMode`，`session_start` 时恢复 |

**关键点：全程没有第二个 agent、没有第二条 LLM 调用链、没有独立节点。** 切换的只是「声明给模型的工具集 + 一段注入的指令文本」。

### 2.3 plan 产出如何进入执行上下文［High］

同一文件里，用户选「Execute the plan」后 [27]：

```ts
planModeEnabled = false;
executionMode = true;
restoreNormalModeTools();
...
const execMessage = `Execute the plan.\n\nRemaining steps:\n${remainingList}\n\nStart with: ${firstTodoItem.text}\nAfter completing a step, include a [DONE:n] tag in your response.`;
pi.sendMessage(planTodoListMessage, { deliverAs: "followUp" });
pi.sendMessage({ customType: "plan-mode-execute", content: execMessage, display: true },
                { triggerTurn: true, deliverAs: "followUp" });
```

即 **plan 的 todo 清单被当作普通 follow-up 用户消息送回同一条 session branch**，由同一个 agent 在同一个上下文里执行。进度追踪靠 `turn_end` 里对 assistant **文本**做 `markCompletedSteps()` 正则提取 `[DONE:n]` 标记 [27]。

### 2.4 从基线到现在有没有变？——没有［High］

blob sha 对比（`gh api contents ...?ref=<tag> --jq .sha`）[32]：

| 文件 | v0.87.1 | v1.0.4 | 相同 |
|---|---|---|---|
| `examples/extensions/plan-mode/index.ts` | `737ce56ac7` | `737ce56ac7` | ✅ |
| `examples/extensions/plan-mode/utils.ts` | `62123f9e39` | `62123f9e39` | ✅ |
| `examples/extensions/subagent/index.ts` | `71b1a33dc7` | `71b1a33dc7` | ✅ |
| `examples/extensions/permission-gate.ts` | `ce29f7eb5c` | `ce29f7eb5c` | ✅ |
| `examples/extensions/structured-output.ts` | `13331377fe` | `13331377fe` | ✅ |

`examples/extensions/` 全量目录 diff：v0.87.1 已有这些文件，1.0.x **只多出 `debug-provider.ts` 和 `jev-router.ts` 两个**，其余同名同在 [30][43]。

### 2.5 pi 对「不同阶段用不同模型」的答案：虚拟模型路由，不是第二个节点［Med］

`docs/virtual-models.md` [53] + `examples/extensions/jev-router.ts` [44]：

> Returning `previous` for `continuation` and `failed` for `retry` keeps prompt caches and thinking signatures valid. Switching models between turns is allowed but loses the prompt cache.

> [jev-router] plans on a strong OpenAI Codex model chosen by the Jev classifier, lets that model make the first edit, and then switches once to a cheaper model, accepting a single prompt-cache miss. It keeps the phase as router state.

这是 pi 里最接近「planner 用一个模型、executor 用另一个模型」的形态，但它是**每个 provider 请求前决定路由到哪个物理模型**（`pi.registerVirtualModel()`），仍在同一个 loop、同一个 transcript 里，且明确记录「切模型 = 丢一次 prompt cache」这一代价。

---

## 3. 子代理［置信度：High］

### 3.1 形态：spawn 独立 pi 进程［High］

`examples/extensions/subagent/index.ts` 顶部注释即结论 [28]：

> Spawns a separate `pi` process for each subagent invocation, giving it an isolated context window. Supports three modes: Single / Parallel / Chain. Uses JSON mode to capture structured output from subagents.

启动参数（`runSingleAgent` 内）[28]：

```
["--mode", "json", "-p", "--no-session"]
+ 可选 "--model" <model>            （agent 未指定时继承 dispatchDefaults.model）
+ 可选 "--thinking" <level>
+ 可选 "--tools" <agent.tools>       （工具面委派）
+ "--append-system-prompt" <tmpfile> （agent 的 systemPrompt，写到 os.tmpdir() 的 mkdtemp 目录，mode 0o600，用完 unlink）
+ 位置参数 "Task: ${task}"
+ spawn 选项 { cwd: cwd ?? defaultCwd, shell: false, stdio: ["ignore","pipe","pipe"] }
```

所以：**上下文隔离 = 进程隔离 + `--no-session`**；子代理默认完全看不到父对话（`pi-durable` 的例子注释同样写 "it does not see this conversation" [13]）。

### 3.2 并行与链式［High］

- `MAX_PARALLEL_TASKS = 8`、`MAX_CONCURRENCY = 4`、`PER_TASK_OUTPUT_CAP = 50 * 1024`；超限直接返回错误而非截断执行 [28]。
- `mapWithConcurrencyLimit` 自己实现并发池；`chain` 模式支持 `{previous}` 占位符替换，任一步失败即停并返回 `isError: true` [28]。

### 3.3 对「black box subagent」的实际回应［Med，含推断］

Armin Ronacher 的批评在上轮报告里被引过。**pi 仓库里没有任何一手材料声称「回应了这条批评」**——以下是我从源码形态做的推断，标注清楚：

- **观测性是渲染层做的，不是协议层做的**：`renderResult` 把子代理的 `getDisplayItems(r.messages)`（assistant 的 text 与 toolCall）一条条画出来，collapsed 时显示 5~10 条并提示 `(Ctrl+O to expand)`；expanded 时分 `─── Task ───` / `─── Output ───` 两段，最后一行 `formatUsageStats()` 打印 `turns / ↑input / ↓output / R cacheRead / W cacheWrite / $cost / ctx / model` [28]。
- **tool `details` 是全量机器可读结果**：`SubagentDetails { mode, agentScope, projectAgentsDir, results: SingleResult[] }`，每个 `SingleResult` 带 `agent / agentSource / task / exitCode / messages / stderr / usage / model / stopReason / errorMessage` [28]。JSON 模式下这些会进 `tool_execution_end.result.details` [24]。
- **项目级 agent 需信任确认**：`confirmProjectAgents` 默认 `true`，project scope 且 `!ctx.isProjectTrusted()` 时弹 `ctx.ui.confirm`，说明「仓库可控的 agent 定义」被视为需人确认的面 [28]。
- **`pi-durable`（experimental）把子代理提升为一等公民**：子对话由 task 持有（ownership index），崩溃重跑能找回同一个 child；`api.details({conversationId})` 让 UI 挂上去；`/agents` 可以在子代理工作时切过去 steer 它；background task 是边界——父的 Esc 到不了它 [13]。

---

## 4. 上下文管理［置信度：High］

### 4.1 compaction 策略［High］

`compaction.md` [17]：

- **触发**：`contextTokens > contextWindow - reserveTokens`（默认 `reserveTokens: 16384`）；在**工具批次结束、下一次 assistant 响应之前**的 `prepareNextTurn` 检查；provider 报 context-overflow 或 `stopReason: "length"` 可触发一次 compact-and-retry；也可 `/compact [instructions]` 手动。
- **保留**：从后往前累计 token 估计到 `keepRecentTokens`（默认 20000）定切点；切点规则为用户消息 / assistant 消息 / BashExecution / custom 消息，**绝不在 tool result 处切**（tool result 必须跟着它的 tool call）。
- **条目**：`CompactionEntry { summary, firstKeptEntryId, tokensBefore, usage?, details?: { readFiles, modifiedFiles } }`；**原始条目保留在 session tree 里，不进模型上下文** [21]。
- **报告**：JSON 流有 `compaction_start {reason: "manual"|"threshold"|"overflow"}` 和 `compaction_end {reason, result{summary, firstKeptEntryId, tokensBefore, estimatedTokensAfter, usage, details}, aborted, willRetry}`，另有 `summarization_retry_scheduled / summarization_retry_attempt_start / summarization_retry_finished` [24]。
- **按模型覆盖**：`compaction.modelOverrides["provider/modelId"]` 可分别覆盖 `reserveTokens` / `keepRecentTokens`，1M 窗口模型的例子是 reserve 提到 400000 [17]。
- **摘要请求主动关缓存写入**：「Summarization requests disable prompt-cache writes because these one-off prompts are unlikely to be reused」[17]。
- **branch summarization**：`/tree` 切分支时可对被放弃的分支做摘要注入，`BranchSummaryEntry`；文件操作列表跨 compaction 与嵌套 branch summary **累计**传递 [17]。

### 4.2 工具结果逐出：`context_edit` 是 append-only 的［High］

`session-format.md` [21]：

> `ContextEditEntry` — Append-only edit of one earlier context-producing entry. It changes only future model context; the target entry and its metadata remain unchanged in raw history, UI, exports, and session accounting. ... `replacement: null` omits the target from model context. ... Edits are branch-relative: navigating to a point before the edit reveals the target's original contribution again.

即「逐出」不是删除，而是一条指向旧条目的、分支相对的省略记录。

### 4.3 prompt-cache 友好性：MCP 热重载毁 cache 的批评有明确回应［High］

这是 0.87.1 → 1.0.x 之间一条清晰的、可引的证据链：

1. **v0.99.2（2026-09-30）** [6]：
   > The `codemode` description no longer includes deferred tools, tool counts, or MCP server instructions, so it **no longer changes when MCP servers connect or change their tools**. The `tool_search` description no longer lists the servers whose tools it can load, for the same reason. Servers are listed instead in an `mcp_servers` system prompt section with a one-line summary, **updated at the start of each prompt; a changed section is appended to the conversation**.

   即：把「会变的部分」从稳定的 prompt 前缀里挪出去，改动改为**追加**到对话，而不是改写前缀。
2. **v1.0.0**：codemode 描述瘦身，GPT-5.6 默认工具集下一次请求从约 5,300 token 降到 3,300 token（约 -40%）[5]。
3. **v1.0.1** [4]：
   > Anthropic tools added or redefined mid-conversation are now defined **inline in the conversation**, so redefining a tool under the same name **keeps the prompt cache** instead of resending the full tool list.
4. **诚实的代价声明**，`extensions.md` [16]：
   > Pi records the initial prompt and tool set in the transcript's first system message, then appends tool and prompt changes before the next model request. **Providers that cannot represent the transition receive a complete transcript checkpoint, which can invalidate the cached prefix.**
5. **缓存保温是一等设置**：`cacheWarming: "off"|"streaming"|"idle"`（默认 `streaming`），只在模型声明 cache lifetime 且预估省下 ≥ $0.05 时才刷；扩展可用 `cache_warming_decision` 事件以 `{action:"warm"|"stop"}` 推翻；保温刷新计入 session 成本但**不进模型上下文**，session 里记成 `kind: "cache_warm"` 的 `UsageEntry` [25][21]。
6. **durable 侧同一原则的另一种表述** [13]：
   > Sections and tool changes are stored as positional system entries in the transcript. Only what changed is sent again, which keeps provider prompt caches warm. **A section that returns something different every time, such as the current time, defeats that.**
   并且每个对话有一个持久 UUIDv7 写进 `pi.provider`、转发给 pi-ai 当 `sessionId`，用于 provider 的 prompt-cache / session affinity，「survives reopen, retries, reset, compaction, and model changes；a child or fork receives a fresh identity」[13]。

---

## 5. 安全 / 审批［置信度：High］

### 5.1 pi 1.0 没有逐调用审批闸门［High］

`security.md` 第一段 [18]：

> Treat model-generated commands and code as untrusted. Pi can read, change, and execute files with the permissions of the account that started it, and it **does not ask for approval before every tool call**. Extensions, package installers, language servers, and other child processes run with those same permissions unless an operating-system or virtualization boundary restricts them.

> Project trust controls which project resources load at startup, but **it does not make that content or the resulting actions safe**. ... Watching the transcript, using project trust, and reviewing changes **do not create a security boundary**.

`settings.md` 里与「批准/权限」相关的设置只有 `defaultProjectTrust: "ask"|"always"|"never"`（且**只能写在 agent 目录设置里**）[25]。源码侧 `ui.confirm` 在 `packages/coding-agent/src` 的命中只有 llama 扩展的「卸载模型？/ 取消下载？」两处和 extension runner 的 `ctx.ui.confirm` 管道，**没有任何内置的 tool-call 前确认** [45]。

### 5.2 审批 = 扩展责任，且给了两份现成配方［High］

- `permission-gate.ts`（example）：对 `bash` 匹配 `rm -rf` / `sudo` / `chmod|chown 777` 时弹 `ctx.ui.select("Allow?", ["Yes","No"])`，否则 `{block:true}`；**`!ctx.hasUI` 时直接 block by default** [29]。
- `extensions.md` 给了「对齐 Codex 审批范围」的配方，基于 MCP 工具注解 [16]：
  ```ts
  pi.on("tool_call", async (event, ctx) => {
    const hints = pi.getAllTools().find(t => t.name === event.toolName)?.annotations;
    const needsApproval =
      hints?.destructiveHint === true ||
      (!hints?.readOnlyHint && ((hints?.destructiveHint ?? true) || (hints?.openWorldHint ?? true)));
    if (needsApproval && !(await ctx.ui.confirm("Allow tool call?", event.toolName)))
      return { block: true, reason: `${event.toolName} was not approved` };
  });
  ```
  注解语义与 MCP 一致：`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`，缺失时取 MCP 默认（非只读、可能破坏、可达开放世界）[16]。

### 5.3 非交互态的信任与超时［High］

- Print / JSON / RPC 模式**无法显示内置 trust 提示**；无 CLI override / 扩展决策 / 已存决策时，`defaultProjectTrust: "always"` 才加载受保护资源，`"ask"`/`"never"` 一律跳过；自动化跑要用 `--approve` / `--no-approve` 显式一次性决定 [18][26]。
- 「超时未答」在 pi 有 UI 层原语但语义不同：`timed-confirm.ts` 演示 `ctx.ui.confirm(title, msg, {timeout: 5000})` 超时自动取消（返回 `false`），或用 `AbortSignal` 手动控制 [46]。**这是「对话框超时 → 视为否」，不是「挂起等人判决」的第三终态**。
- 隔离建议 [18][47]：plain Docker / Docker Sandboxes（provider 凭证留在宿主、由代理替换）/ OpenShell / Gondolin 扩展（micro-VM，宿主 Pi + 远端工具执行）四种；`sandbox` example 用 `@anthropic-ai/sandbox-runtime` 在 OS 层限制 bash 的文件系统与网络（macOS `sandbox-exec`、Linux `bubblewrap`）[48]。

---

## 6. 扩展性（对 #67 直接相关）［置信度：High］

### 6.1 工具编排面：0.87.1 → 1.0.x 的最大增量［High］

v0.99.0 release notes 一次列全，之后没再退 [8]：

> Added extension tool APIs for orchestrating tools: `exposure` (`direct`, `model-only`, `codemode`, `deferred`, or `hidden`), `namespace`, `annotations`, `outputSchema` with `structuredContent`, `isError` results, `prepareLoadout()`, and `ctx.executeTool()` for nested tool calls, which emit events with `parentToolCallId` and are recorded as bounded `nestedCalls` on the calling tool's result.

`extensions.md` 的定义 [16]：

| exposure | 语义 |
|---|---|
| `direct`（默认） | 激活时声明给模型，且激活时可被别的工具调 |
| `model-only` | 只声明给模型，永不可被调（给「编排其他工具的/问用户的」工具用） |
| `codemode` | 只要注册就可被调，`codemode` 工具会列它；不显式激活不声明给模型 |
| `deferred` | 同 `codemode`，但 codemode 不列它；`tool_search` 能找到并激活 |
| `hidden` | 注册但不可达；**要撤回一个工具就再用 `hidden` 重注册，因为工具不能注销** |

- `namespace: {name, description, instructions}` 分组（MCP server 也是这样）；`instructions` 不列出来，由 codemode 脚本用 `describeNamespace(name)` 读 [16]。
- `prepareLoadout(loadout)`：工具激活集合变化时被调，能替换已声明工具的描述，并给出 `hiddenDeclarations`（保持激活与可调、但请求里不带声明）。默认系统提示会把隐藏工具从工具表与规则里去掉、skills 提示里不提它——**编排者得自己把准则告诉模型**。`codemode` 自己只用了这个 hook + `exposure` + `ctx.executeTool()` [16]。
- `ctx.executeTool(name, args, {signal, onUpdate})`：嵌套调用**照走参数校验和 `tool_call`/`tool_result` handler**，emit 带 `parentToolCallId`，`toolCallId` 形如 `<parent id>/<n>`；嵌套调用**不产生 transcript 条目**，只在调用者结果上留一条有界记录 `nestedCalls`（name/arguments/status/duration/error，**永不存结果**；单调用 >8 KiB、单结果 >32 KiB 省略，最多 256 条，`complete:false` 标记有丢失）；嵌套结果的 `usage` 逐层加到调用者的结果 `usage` 上 [16]。
- `pi.registerToolRenderer((toolName, next) => renderers)` 能为**尚未注册**的工具（如 resume 时 server 还没连上的 MCP 工具）指定渲染 [16]（v1.0.1 新增）[4]。

### 6.2 官方 extensions 清单［High］

- **built-in 扩展共 4 个**：`mcp`、`llama`（llama.cpp provider）、`codemode`、`tool-search`；可全局/按项目在 `pi config` 里关，记成 `extensions` 设置里的 `-builtin:<name>`；SDK inline 扩展用 `builtin: true`  opt in [8][26][16]。CLI 的 built-in 扩展是 `replaceable: true` 的——别的扩展同名注册就让位 [25]。
- `--no-extensions` 会把 built-in 也一起关掉，要单独留一个得 `pi -ne -e builtin:mcp` [26]。
- **examples 扩展**：`plan-mode/`、`subagent/`、`sandbox/`、`with-deps/`、`custom-provider-*/`、`gondolin/`、`dynamic-resources/`、`doom-overlay/` + 约 70 个单文件 `.ts`；1.0.x 相比 v0.87.1 只多 `debug-provider.ts`（`provider_stream_event` 查看器）和 `jev-router.ts`（虚拟模型路由）[30][43]。

### 6.3 分发形态（能力包可拆装）［High］

`packages.md` [22]：

- `pi install npm:@scope/pkg@1.0.0` / `git:github.com/...@v1` / `./local`；`pi list` / `pi remove` / `pi update --extensions`；`--local` 写进 `.pi/settings.json`（**项目包要 trust 之后才装才加载**）。
- 包 = 普通目录或 npm 包；无 manifest 时按约定目录发现 `extensions/ skills/ prompts/ themes/`；有 manifest 时 `package.json` 的 `pi` key 显式列 glob。
- `keywords: ["pi-package"]` 让它进 pi.dev 包 gallery。
- **宿主提供给扩展的模块**：`pi-ai` / `pi-agent-core` / `pi-coding-agent` / `pi-tui` / `typebox`；这些要写进 `peerDependencies` 且 `"*"`，**不要写进 `dependencies`**（物理副本会在编译 ESM 里绕过模块映射，造成重复 class/registry）。
- 装的包各有独立 module root，**不能假设两个包共享同一份依赖实例**。
- `pi config` 可按资源类型（extensions/skills/prompts/themes）过滤加载，支持 `[]` / `!glob` / `+path` / `-path`。

---

## 7. 可评测性［置信度：High］

### 7.1 CLI 表面：四种模式同一套 agent［High］

`cli-integration.md` 的模式表 [49]：

| 模式 | 接口 | 生命周期 | 用途 |
|---|---|---|---|
| Interactive | TUI | 到用户退出 | 人直接用 |
| Print (`-p`/`--print`) | stdout 只出最终 assistant 文本 | 一次调用 | 脚本要最终答案 |
| JSON (`--mode json`) | stdout 出 JSONL 事件 | 一次调用 | 进程要结构化进度 |
| RPC (`--mode rpc`) | stdin 收 JSONL 命令、stdout 出响应+事件 | 长驻 | 进程要双向控制 |

`cli.md` / `cli-integration.md` 里对评测台有用的开关 [26][49]：

- `--tools <list>` / `--exclude-tools <list>`：**支持 `*` 通配**（v1.0.4 新增，例：`--tools read,codemode,'mcp__radius__*'`）；`--tools` 现在默认保留 MCP 工具，只有条目以 `mcp__` 开头才过滤 MCP [1]。
- `--no-mcp`：单次运行关掉内置 MCP 支持（v1.0.4 新增）[1]。
- `--no-session`：内存态会话，不落盘。
- `--approve` / `--no-approve`：一次性 trust 决定，非交互态必需。
- `--no-extensions` / `-ne` + `-e builtin:<name>`：精确控制加载哪些扩展。
- `--offline`（等价 `PI_OFFLINE=1`）：关掉模型目录刷新等自动网络活动。
- `--append-system-prompt <text|path>`：注入子代理/变体指令（subagent 例正是用它传 agent systemPrompt）。
- `--session-dir` / `--session-id` / `--fork` / `--continue` / `--resume`：会话寻址。
- `--export <input> [output]`：把 session 文件导成 HTML。
- `pi auth check --provider X --json`：退出码 `0/1/2` = `ready / not_ready / invalid`，可在评测前做 key gate [26]。

### 7.2 输出结构化程度［High］

- **JSON 模式是严格 JSONL**：一条一 JSON 对象，LF 结尾；**split 只能按 LF**，不能 `\u2028/\u2029`；`readline` 不适用；stdout 专用于 JSONL，诊断走 stderr；必须持续读 stdout，否则管道满会卡住 pi [24]。
- **终止信号是三级的**：`agent_end`（一个 low-level run 结束，自动重试/overflow 恢复/steering/follow-up 可能还有）→ **`agent_settled`（pi 不会再自动继续）** [24][49]。这对评测台是关键的等待条件。
- **Print 模式的退出码**：最终 assistant 响应 `stopReason` 为 `error`/`aborted` 时**非零退出**；**JSON 模式不因此非零退出**——「失败/中止的响应会出现在事件流里，但本身不产生非零退出码」，要判成败必须查事件 [49]。
- **结构化输出的现成形态**：`structured-output.ts` 例用 `terminate: true` 让 agent 以一次工具调用收尾、省掉额外一轮 LLM；工具侧还有 `outputSchema` + `structuredContent`（模型看 `content`，codemode 脚本等程序化调用方拿 `structuredContent`），出错但要带数据时返回 `isError: true` 而非 throw [50][16]。

### 7.3 pi 自己就是「用 JSON 模式驱动子进程」的先例［High］

subagent 例的参数是 `["--mode","json","-p","--no-session"]`，然后逐行 parse 子进程 stdout 的 `message_end` / `tool_result_end` 事件 [28]。**「外层 harness spawn 内层 pi、用 JSONL 收事件」这个模式是一等 example 代码，不是 hack。**

### 7.4 pi 自己的评测包［High］

`packages/evals` [51]：用 `vitest-evals`，`PI_PROVIDER` / `PI_MODEL` 两个环境变量驱动；`eval:host`（宿主跑 smoke + documentation-audit）与 `eval:docs`（Docker 双镜像 `without_docs` / `with_docs` 配对比 lift）。产物里有 `protocol.json`（模型/镜像/cases/任务/protocol digest）、`expected-runs.json`、`observations.jsonl`、每 arm 的 `vitest.json`、`<variant>/sessions/*/session.jsonl`、`report.json|txt`。**「一对 arm 必须双方都恰好产出一个分数才计入 lift；缺失/重复/跳过/未评分/出错都阻断该对；任一对被阻断就撤回 headline pass rate」**——这套防作弊纪律值得单独看。文档 eval 默认只放 `read/write/edit/grep/find/ls`，不给 bash 和 web 搜索。

---

## 8.（并入上文）小结：七个问题的答案密度

| 问题 | 一句话答案 | 置信度 |
|---|---|---|
| 1. 1.0 发布本体 | v1.0.0 = 2026-10-01，最新 v1.0.4 = 2026-10-05；破坏性变更在 agent core 的 harness 摘除 + 包结构增删；无 semver 政策一手证据 | High / Low(末条) |
| 2. plan/execute | 一个 `agentLoop`，无 planner 节点；plan mode = 同 agent 的工具面+指令切换；plan 作为 follow-up 用户消息回到同一 branch；与基线字节级相同 | High |
| 3. 子代理 | 仍是 spawn 独立 `pi --mode json -p --no-session` 进程；并行 ≤8/并发 ≤4 + chain；可观测性在渲染与 tool details | High |
| 4. 上下文管理 | threshold compaction（reserve 16384 / keepRecent 20000）+ 分支摘要 + `context_edit` 逐出；缓存友好靠「稳定前缀 + 变化追加以防 + Anthropic inline 工具定义」 | High |
| 5. 安全/审批 | 无内置逐调用审批；project trust 只管资源加载；审批是 `tool_call` block + `ctx.ui.confirm`；隔离靠容器/OS sandbox | High |
| 6. 扩展性 | `exposure`/`namespace`/`annotations`/`prepareLoadout`/`ctx.executeTool`/`registerVirtualModel`/`registerMcpServer`/`registerToolRenderer` 是 0.87.1 后新增面；4 个 built-in 扩展；pi packages 分发 | High |
| 7. 可评测性 | 四模式 CLI；JSON 模式严格 JSONL + `agent_settled`；Print 非交互退出码非零、JSON 不非零；`PI_PROVIDER`/`PI_MODEL` + evals 包 | High |

---

## 9. 对 langgraph-agent 的启发

> 立场声明：以下是摆事实与启发，不写成实施方案。每条都挂了证据；标［推断］的是我从形态做的推断，不是 pi 的一手陈述。

### 9.1 可直接借的形态

**A1. 能力包注册表应该有三轴，而不是「装/不装」一轴。**
pi 的 `exposure` 把「这个能力对模型意味着什么」拆成 direct / model-only / codemode / deferred / hidden 五态，再用 `namespace{name,description,instructions}` 分组、`prepareLoadout()` 让编排者决定「激活但不下发声明」[16]。对 #67 的直接对应：**注册表除了「已安装」，还要记「声明给模型 / 可被别的工具调 / 延迟检索后激活」**。证据强度：这套面在 v0.87.1 的 `extensions.md` 里 0 命中、v1.0.4 有 17 处 [35]，是这段时间真实的增量方向，不是历史包袱。

**A2. planner 与 executor 合成「一个 agent 的模式切换」，且 plan 必须作为对话内容回到执行上下文。**
pi 的 plan-mode 用 `pi.setActiveTools()` 收窄工具面 + `before_agent_start` 注入一条隐藏 custom message，执行阶段把 todo 清单当 `deliverAs: "followUp"` 的用户消息送回来 [27]。这正面回答了委托方的问题：**在 pi 的形态里「plan 不进 executor 上下文」不可能发生，因为 plan 就是 transcript 本身**。可借的具体形状：(a) 模式 = 工具集 + 一段可撤销的注入指令（注意 pi 还用 `context` 事件**反向清理**过期 plan 指令，这个细节容易漏）；(b) 阶段产出 = 普通消息，不建旁路状态。

**A3. 子代理的「结果回填」比「结果返回」更接近解决 black box。**
subagent 例把子进程的 assistant text、每一次 toolCall、以及每任务的 `turns/↑/↓/R/W/$/ctx/model/stopReason` 全部塞进 tool `details` 和渲染层 [28]。对「委托方要做跨 agent 评测台」的含义：**子代理至少要把 usage/cost/failure reason 变成父转录里的一等字段**，否则无法在评测里归因。这是形态可借；但见 T2 关于「pi 无一手材料声称回应了 Armin」的限定。

**A4. 上下文逐出用「append-only、分支相对的 context_edit」，不用删除。**
`ContextEditEntry` 的 `replacement: null` 只影响未来模型上下文，原始条目在 raw history / UI / exports / 记账里原样保留； navigation 到编辑之前的点，被逐出内容的原始贡献会重新出现 [21]。对委托方「上下文压缩 + 工具结果逐出」的直接启发：**逐出是一条可审计的追加记录，压缩是另一种追加记录（`CompactionEntry` 带 `tokensBefore`/`estimatedTokensAfter`/`firstKeptEntryId`）**，两者都不销毁证据。JSON 流里的 `compaction_start/compaction_end(reason, result, aborted, willRetry)` 就是现成的留痕面 [24]。

**A5. 压缩预算与缓存预算分开配，且摘要请求主动不写缓存。**
`reserveTokens`（触发阈值 + 摘要输出上限）/ `keepRecentTokens`（逐字保留）/ `modelOverrides`（按模型分设）+ 「Summarization requests disable prompt-cache writes」[17]。委托方若按模型分设压缩策略，这是现成的参数形状。

**A6. 缓存友好的工程做法是「稳定前缀 + 变化追加」，而且 pi 为此重构过工具描述。**
v0.99.2 把会随 MCP 连接而变的 `codemode` 描述改成稳定版、server 列表移到「每次 prompt 开头更新、变了就追加进对话」的 section；v1.0.1 让 Anthropic 的增量工具定义走 inline [6][4]。**委托方若担心「能力包热插拔毁 prompt cache」，pi 给出的答案是「不要让包内容进入稳定前缀」**。同时 pi 也诚实地写了代价：不能表达这种迁移的 provider 会收到完整 transcript checkpoint、击穿缓存前缀 [16]。

**A7. 扩展/能力「按名存储、代码可换」。**
`pi-durable`：conversation 只存 extension **names**，uninstall 后「conversations that select it just stop getting it until it is installed again」；`registry.install(同名)` 原地替换；**已经开始的工作保持它取的那份代码**（running tool call 在旧实现下跑完，每个 task phase 在 phase 开始时解析一次 hooks/agent）[13]。对 #67「可拆装」：这是「拆装不打断在飞任务」的一个具体形态。注意该包自称 experimental（见 T2）。

**A8. 并发与中止的所有权语义可以直接对照子任务图。**
durable 的 task 有 owner edge、`waiting(on, policy: failFast|allSettled)`、`completing`（ outcome 已定但要等它持有的工作跑完才 terminal）、abort **bottom-up**（先中止它持有的工作，再跑自己的 abort handler）、`background: true` 的任务是边界（父的 abort 到不了它，`root.abort({background:true})` 才可以）[13]。委托方的 `mode="subtask"` 子任务图 + 熔断/stop 语义，这套是所有权的另一种表达，值得逐条比对自己的 stop/resume 语义。

**A9. 评测面的现成驱动形状。**
`--mode json`（严格 LF JSONL + `agent_settled` 作为终止条件）+ `--print`（最终文本 + error/aborted 非零退出）+ `--no-session` + `--tools/--exclude-tools` 通配 + `--no-mcp` + `--approve/--no-approve` + `auth check --json` 的 0/1/2 key gate [26][49][24][1]。**且 pi 自己的 subagent 例就是用 `--mode json -p --no-session` spawn 子进程** [28]——把 pi 当黑箱子进程跑是一等模式。另外 `packages/evals` 的成对 arm 纪律（双方都必须恰好一个分数、任一对被阻断就撤回 headline）是防自欺的现成样本 [51]。

### 9.2 需要自行验证的权衡

**T1. pi 没有内置审批闸门——它是反面参照，不是可借形态。**
`security.md` 明说不逐调用审批，trust 只 gates 资源加载 [18]；`permission-gate.ts` 是 example 且 **`!ctx.hasUI` 时 block by default** [29]；`timed-confirm.ts` 的 timeout 语义是「对话框超时 → 视为否」[46]，**不等于「超时未答 = 挂起待决」这第三终态**。所以委托方的三档终态（人明确拒绝 / 超时未答 / 停止打断）在 pi 侧**没有对应物**。若要把 pi 拉进跨 agent 闸门评测，必须自己写扩展把三态造出来，并且要先决定：非交互态下第三态是 block 还是 park——pi 的 example 默认选了 block。

**T2. 「1.0 = 稳定」这个前提本身未验证，且至少有一个包明确不承认。**
仓库与维护者博客都找不到 semver 政策声明（博客最新一篇 2026-05-30，无 1.0 公告）[39][40]；而 `pi-durable` README 开头即 `**Experimental.** The API changes without notice between releases.` [13]。**A7/A8 两条启发恰好落在 experimental 包上**——要借就得接受 API 会变。另外 v1.0.1 把发布包里的 `npm-shrinkwrap.json` 删了、npm 装法不再钉间接依赖 [4]，部署可复现性要靠 pi.dev installer，这是独立的运维决策点。

**T3. 工具声明变更会击穿缓存前缀，pi 只是把成本挪位而非消除。**
`extensions.md` 原话：「Providers that cannot represent the transition receive a complete transcript checkpoint, which can invalidate the cached prefix」[16]。委托方现在的形态是 **planner 和 executor 两个节点各发一次 LLM 调用**——若改成「一个 agent 的模式切换」而模式切换伴随工具集切换，就正好落进这条代价里。**要自己测 cache hit 率，不能假定 pi 的做法零成本。**

**T4. 子代理 = 进程级隔离，代价是启动开销 + 上下文零共享。**
每次 invocation spawn 一个新 pi 进程（QuickJS/codemode 之外还要 Node/Bun 进程），子代理「does not see this conversation」[28][13]。适合自包含任务，**不适合「带着父上下文的小续跑」**。若委托方的子任务图依赖父上下文，需要自行设计注入通道——pi 只给 `task` 字符串 + 可选的 systemPrompt 临时文件 + `--tools`。

**T5. plan-mode 的进度是文本约定，不是状态机。**
`[DONE:n]` 标记由 `markCompletedSteps()` 从 assistant 文本里正则提取，`turn_end` 时更新；会话恢复时还要**重扫**「最后一个 `plan-mode-execute` 条目之后」的 assistant 文本来重建完成态 [27]。委托方的完成验证/熔断是状态字段驱动的，这条形态更轻但更脆——若照抄，要把「进度从哪里重建」当独立问题回答（pi 的答案是「重放文本」，代价已在代码注释里）。

**T6. pi 把「planner 位」让给了虚拟模型路由，而它明确接受一次 cache miss。**
`jev-router.ts`：分类器选强模型出第一版 → 切一次便宜模型，注释写 "accepting a single prompt-cache miss" [44]；`virtual-models.md` 也说跨 turn 切模型会丢 cache [44]。若委托方把 planner/executor 合成一个 agent，需要自己回答 pi 已经明确定价的两个问题：**模式切换换不换模型？换模型的 cache 代价记在哪笔账上？**

---

## 来源清单

编号与正文引用一一对应。Tier 1 = GitHub Releases / Tags / 源码 / 官方文档（`@tag` 固定）；Tier 2 = 背景参考。

### 发布与版本（Tier 1）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 1 | Release **v1.0.4**（含 `--tools` 通配 / `--no-mcp` / codemode 图片等） | https://github.com/earendil-works/pi/releases/tag/v1.0.4 | 2026-10-05 | Tier 1 |
| 2 | Release **v1.0.3**（Breaking：Azure provider `azure-openai-responses` → `azure`） | https://github.com/earendil-works/pi/releases/tag/v1.0.3 | 2026-10-05 | Tier 1 |
| 3 | Release v1.0.2（`samplingParamsByThinkingLevel`；正文未直接引用） | https://github.com/earendil-works/pi/releases/tag/v1.0.2 | 2026-10-04 | Tier 1 |
| 4 | Release **v1.0.1**（Nix flake / 项目级 MCP 覆盖 / `registerToolRenderer` / Anthropic inline 工具定义 / Removed `npm-shrinkwrap.json`） | https://github.com/earendil-works/pi/releases/tag/v1.0.1 | 2026-10-03 | Tier 1 |
| 5 | Release **v1.0.0**（无 Breaking Changes 段；fullscreen 默认；codemode -40% token） | https://github.com/earendil-works/pi/releases/tag/v1.0.0 | 2026-10-01 | Tier 1 |
| 6 | Release **v0.99.2**（MCP 描述稳定化、`mcp_servers` section 追加、首 prompt 不等 MCP） | https://github.com/earendil-works/pi/releases/tag/v0.99.2 | 2026-09-30 | Tier 1 |
| 7 | Release v0.99.1（GPT-6.1 Sol；正文未直接引用） | https://github.com/earendil-works/pi/releases/tag/v0.99.1 | 2026-09-29 | Tier 1 |
| 8 | Release **v0.99.0**（codemode / tool_search / MCP 成为 built-in；**工具编排 API 全套新增**） | https://github.com/earendil-works/pi/releases/tag/v0.99.0 | 2026-09-29 | Tier 1 |
| 9 | Release **v0.87.1**（上轮调研基线；正文未直接引用，作为对比锚点保留） | https://github.com/earendil-works/pi/releases/tag/v0.87.1 | 2026-09-22 | Tier 1 |
| 31 | `gh api repos/earendil-works/pi/releases --jq '.[0:10][]."\(.tag_name) \(.published_at)"'` + `gh api .../tags`：版本号与发布日期的取证锚（前 10 release / 前 15 tag） | https://github.com/earendil-works/pi/releases | 2026-10-07 查询 | Tier 1 |
| 33 | `gh api repos/earendil-works/pi`：`default_branch=main`、`license=MIT`、113k stars | https://github.com/earendil-works/pi | 2026-10-07 查询 | Tier 1 |
| 41 | `README.md` @ v1.0.4：Nix flake `github:earendil-works/pi/stable` 指向最新 release；release 归档带 `SHA256SUMS` | https://github.com/earendil-works/pi/blob/v1.0.4/README.md | 2026-10-05 | Tier 1 |
| 42 | `README.md` @ v1.0.4（安装说明）：直接外部依赖钉死精确版本，内部 workspace 包版本共管 | https://github.com/earendil-works/pi/blob/v1.0.4/README.md | 2026-10-05 | Tier 1 |

### 包结构与 Agent Loop（Tier 1）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 10 | `packages/agent/CHANGELOG.md` @ v1.0.4：`[1.0.0]` 的 Breaking Changes = 从 `@earendil-works/pi-agent-core` 摘除实验性 harness | https://github.com/earendil-works/pi/blob/v1.0.4/packages/agent/CHANGELOG.md | 2026-10-05 | Tier 1 |
| 11 | `packages/agent/README.md` @ v1.0.4：事件序列、`toolExecution` parallel/sequential、`beforeToolCall`/`terminate`、steering/follow-up、transcript 拥有 system prompt 与工具声明 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/agent/README.md | 2026-10-05 | Tier 1 |
| 12 | `packages/agent/src/agent-loop.ts` @ v1.0.4：`agentLoop` / `agentLoopContinue` / `runAgentLoop` | https://github.com/earendil-works/pi/blob/v1.0.4/packages/agent/src/agent-loop.ts | 2026-10-05 | Tier 1 |
| 15 | `docs/how-pi-works.md` @ v1.0.4：Agent loop / Context / Sessions / Interfaces / Trust 五节 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/how-pi-works.md | 2026-10-05 | Tier 1 |
| 34 | `gh api git/trees/v0.87.1?recursive=1` vs `v1.0.4` 包结构 diff（删 `session-backends`，增 `codemode`/`env`/`mcp`）+ 各 `packages/*/package.json` 的 name/version/exports | https://github.com/earendil-works/pi/tree/v1.0.4/packages | 2026-10-07 查询 | Tier 1 |
| 37 | `gh api contents/packages/coding-agent/docs?ref=v0.87.1` vs `v1.0.4`：只新增 `codemode.md`/`mcp.md`/`virtual-models.md`，零删除 | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/docs | 2026-10-07 查询 | Tier 1 |

### 扩展性与能力包（Tier 1）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 16 | `docs/extensions.md` @ v1.0.4：`exposure` 五态 / `namespace` / `annotations` / `prepareLoadout` / `ctx.executeTool` 与 `nestedCalls` / `registerToolRenderer` / Codex 式审批配方 / 工具激活与缓存代价 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/extensions.md | 2026-10-05 | Tier 1 |
| 22 | `docs/packages.md` @ v1.0.4：pi packages 安装 / `pi` manifest key / `pi-package` keyword / 宿主提供模块与 peerDependencies 规则 / 资源过滤 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/packages.md | 2026-10-05 | Tier 1 |
| 35 | `docs/extensions.md` @ **v0.87.1**：对 `exposure`/`namespace`/`prepareLoadout`/`executeTool`/`annotations`/`outputSchema`/`registerVirtualModel`/`registerMcpServer`/`registerToolRenderer` **0 命中**，全文仅 1 处 `setActiveTools`（vs v1.0.4 的 17 处） | https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/extensions.md | 2026-10-07 查询 | Tier 1 |
| 38 | `packages/coding-agent/src/extensions/{codemode,llama,mcp,tool-search}` + `src/core/source-info.ts`（`builtin:<name>` 命名） | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/src/extensions | 2026-10-05 | Tier 1 |
| 30 / 43 | v0.87.1 与 v1.0.4 的 `examples/extensions/` 目录对比：1.0.x 仅多 `debug-provider.ts`、`jev-router.ts`，其余同名同在 | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/examples/extensions | 2026-10-07 查询 | Tier 1 |

### plan mode / subagent / 审批 / 隔离（Tier 1）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 27 | `examples/extensions/plan-mode/index.ts` @ v1.0.4：`setActiveTools` 模式切换、`tool_call` 白名单 block、`before_agent_start` 注入 `[PLAN MODE ACTIVE]`、`context` 反向清理、todo 作 `deliverAs:"followUp"` 送回、`[DONE:n]` 文本追踪、resume 重扫 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/plan-mode/index.ts | 2026-10-05 | Tier 1 |
| 28 | `examples/extensions/subagent/index.ts` @ v1.0.4：spawn 独立 `pi --mode json -p --no-session`、`--model/--thinking/--tools/--append-system-prompt`、MAX_PARALLEL_TASKS=8 / MAX_CONCURRENCY=4、chain `{previous}`、`onUpdate` 流式、`SubagentDetails` 全量 usage/cost/stopReason、renderCall/renderResult 观测 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/subagent/index.ts | 2026-10-05 | Tier 1 |
| 29 | `examples/extensions/permission-gate.ts` @ v1.0.4：`rm -rf`/`sudo`/`chmod 777` 弹确认，`!ctx.hasUI` 时 block by default | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/permission-gate.ts | 2026-10-05 | Tier 1 |
| 30 | `gh api repos/earendil-works/pi/contents/packages/coding-agent/examples/extensions?ref=v0.87.1 --jq '.[].name'`：v0.87.1 顶层清单（§2 目录 diff 的基线侧；复核确认基线侧**没有** debug-provider.ts / jev-router.ts） | https://github.com/earendil-works/pi/tree/v0.87.1/packages/coding-agent/examples/extensions | 2026-10-07 查询（评审补录） | Tier 1 |
| 43 | `gh api repos/earendil-works/pi/contents/packages/coding-agent/examples/extensions?ref=v1.0.4 --jq '.[].name'`：v1.0.4 顶层清单（diff 对照侧；较基线**只多** debug-provider.ts / jev-router.ts 两个文件、零删除） | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/examples/extensions | 2026-10-07 查询（评审补录） | Tier 1 |
| 46 | `examples/extensions/timed-confirm.ts` @ v1.0.4：`ctx.ui.confirm(..., {timeout: 5000})` 超时自动取消（返回 `false`）/ `AbortSignal` 手动控制 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/timed-confirm.ts | 2026-10-05 | Tier 1 |
| 50 | `examples/extensions/structured-output.ts` @ v1.0.4：`terminate: true` 以工具调用收尾、省掉额外一轮 LLM | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/structured-output.ts | 2026-10-05 | Tier 1 |
| 48 | `examples/extensions/sandbox/index.ts` @ v1.0.4：`@anthropic-ai/sandbox-runtime` 在 OS 层限制 bash 的文件系统与网络（macOS `sandbox-exec` / Linux `bubblewrap`） | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/sandbox/index.ts | 2026-10-05 | Tier 1 |
| 18 | `docs/security.md` @ v1.0.4：「does not ask for approval before every tool call」；project trust 只管资源加载；三种运行方式的边界表 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/security.md | 2026-10-05 | Tier 1 |
| 47 | `docs/containerization.md` @ v1.0.4：plain Docker / Docker Sandboxes（provider 凭证留宿主由代理替换）/ OpenShell / Gondolin 四种隔离方法 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/containerization.md | 2026-10-05 | Tier 1 |
| 45 | `packages/coding-agent/src` 内 `ui.confirm` 命中点：仅 llama 扩展两处 + extension runner 的 `ctx.ui.confirm` 管道；无内置 tool-call 前确认 | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/src | 2026-10-05 | Tier 1 |
| 25 | `docs/settings.md` @ v1.0.4：`defaultProjectTrust`（仅 agent 目录设置）/ `cacheWarming` / `defaultTools` 的 `+name`/`-name` / `builtin: true` inline 扩展语义 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/settings.md | 2026-10-05 | Tier 1 |
| 32 | `gh api contents/<path>?ref=v0.87.1 \| v1.0.4 --jq .sha`：`plan-mode/index.ts`(`737ce56ac7`)、`plan-mode/utils.ts`(`62123f9e39`)、`subagent/index.ts`(`71b1a33dc7`)、`permission-gate.ts`(`ce29f7eb5c`)、`structured-output.ts`(`13331377fe`) 两边 blob sha **完全相同** | https://github.com/earendil-works/pi | 2026-10-07 查询 | Tier 1 |

### 上下文 / Session / 可评测性（Tier 1）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 17 | `docs/compaction.md` @ v1.0.4：触发公式、`reserveTokens`/`keepRecentTokens`/`modelOverrides`、切点规则、`CompactionEntry`/`BranchSummaryEntry` 结构、摘要格式、扩展 hook、摘要请求关缓存写入 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/compaction.md | 2026-10-05 | Tier 1 |
| 21 | `docs/session-format.md` @ v1.0.4：JSONL 树、`session`/`message`/`model_change`/`usage`/`compaction`/`context_edit`/`branch_summary`/`custom`/`custom_message`/`label`/`session_info` 条目类型与 build 流程 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/session-format.md | 2026-10-05 | Tier 1 |
| 13 | `packages/durable/README.md` @ v1.0.4（自述 Experimental）：registry 按名 install/replace、conversation 存 extension names、section 作为 positional system entry 保缓存、per-conversation UUIDv7 作 `sessionId`、task 所有权 / `waiting(on, policy)` / `completing` / abort bottom-up / background 边界、child task、task graph、compaction | https://github.com/earendil-works/pi/blob/v1.0.4/packages/durable/README.md | 2026-10-05 | Tier 1 |
| 26 | `docs/cli.md` @ v1.0.4：`--tools`/`--exclude-tools` 通配、`--no-mcp`、`--no-session`、`--approve`/`--no-approve`、`--offline`、`--append-system-prompt`、`pi auth check` 退出码 0/1/2、`pi mcp` 子命令 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/cli.md | 2026-10-05 | Tier 1 |
| 49 | `docs/cli-integration.md` @ v1.0.4：四模式对照表、Print 模式 final error/aborted 非零退出、JSON 模式失败**不**非零退出、`agent_settled` 语义、stdout/stderr 分工 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/cli-integration.md | 2026-10-05 | Tier 1 |
| 24 | `docs/json.md` @ v1.0.4：严格 JSONL（LF-only、`readline` 不适用、必须持续读）、事件全表、`compaction_start/compaction_end`、`auto_retry_*`、`summarization_retry_*`、`agent_settled` | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/json.md | 2026-10-05 | Tier 1 |
| 51 | `packages/evals/README.md` @ v1.0.4：`vitest-evals` + `PI_PROVIDER`/`PI_MODEL`、Docker 双 arm（`without_docs`/`with_docs`）配平、产物清单、成对 arm 的阻断纪律 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/evals/README.md | 2026-10-05 | Tier 1 |

### 虚拟模型 / 反向确认（Tier 1 + Tier 2）

| # | 标题 | URL | 日期 | 层级 |
|---|---|---|---|---|
| 44 | `examples/extensions/jev-router.ts` @ v1.0.4：强模型规划 → 切便宜模型执行，"accepting a single prompt-cache miss"，phase 存 router state | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/examples/extensions/jev-router.ts | 2026-10-05 | Tier 1 |
| 53 | `docs/virtual-models.md` @ v1.0.4：`previous`/`failed` 的 `continuation`/`retry` 决策保 cache；跨 turn 切模型丢 cache；`pi.registerVirtualModel()` 与 `/session` 按物理模型分列成本 | https://github.com/earendil-works/pi/blob/v1.0.4/packages/coding-agent/docs/virtual-models.md | 2026-10-05 | Tier 1 |
| 52 | 本仓库上轮调研报告 `docs/DEEP_RESEARCH_pi-vs-langgraph-agent.md`（基线 v0.87.1；正文未直接引用——本报告一律以一手复核为准，**未采用**其未经验证的事实陈述） | `E:\code\demo\langgraph-agent\docs\DEEP_RESEARCH_pi-vs-langgraph-agent.md` | 基线 2026-09-22 | Tier 2 |
| 36 | 检索：`packages/coding-agent/docs` 全文匹配 `planner\|planning agent\|plan agent` → **0 命中**（v1.0.4） | https://github.com/earendil-works/pi/tree/v1.0.4/packages/coding-agent/docs | 2026-10-07 查询 | Tier 1 |
| 39 | 检索：`README.md` / `CONTRIBUTING.md` / `AGENTS.md` / `docs/index.md` / `docs/settings.md` 中 semver / API 稳定性 / 「1.0 = stable」声明 → **0 命中**（v1.0.4） | https://github.com/earendil-works/pi/tree/v1.0.4 | 2026-10-07 查询 | Tier 1 |
| 40 | Mario Zechner 博客 posts 索引（维护者一手站点；用于反向确认「无 1.0 发布公告」，最新一篇 2026-05-30） | https://mariozechner.at/posts/ | 2026-10-07 查询 | Tier 1（站点） |

### 未找到一手证据的事项（明确记录，不补脑补）

1. **pi 1.0 的 semver / API 稳定性政策声明**——仓库与维护者博客均检索不到（[39][40]）。唯一可引的「稳定」是 Nix `stable` flake 与 release `SHA256SUMS`（[41]）。
2. **plan-mode / subagent 在 1.0「转正为 built-in」**——实证相反：仍为 `examples/extensions/` 下的扩展，且 blob sha 与 v0.87.1 完全相同（[32]）；built-in 扩展只有 `mcp`/`llama`/`codemode`/`tool-search` 四个（[38]）。
3. **pi 对 Armin Ronacher「black box subagent」批评的一手回应**——仓库内无任何材料声称回应；§3.3 的观测性描述为源码形态推断。
4. **1.0 的「扩展 API 稳定性承诺」**——`exposure` 等编排面自 v0.99.0 引入后无破坏性变更记录，但没有任何地方承诺它不再变。
5. **`pi-durable` 的 API 稳定性**——README 自述 `**Experimental.** The API changes without notice between releases.`（[13]），§9.1 的 A7/A8 两条启发正落在该包上。
