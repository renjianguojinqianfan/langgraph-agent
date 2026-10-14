# pi 的 agent 是怎么构建出来的（源码级调研）

> 生成 2026-10-14 ｜ 证据基准：tag **v1.2.0**（commit `06a466935536f8c9bcacb2d5efc0fc021be1bdff`，发布 **2026-10-12T23:03:53Z**，默认分支 `main`，MIT，114,951 stars）[1][2][3]
> 方法：GitHub 源码 + 官方 docs 一手来源（`raw.githubusercontent.com` @ `v1.2.0` 与 GitHub contents API 固定 tag / blob sha）+ 编号引用。
> **本报告的「源码实证」与「推断」分栏标注。** 凡一手源查不到的写「**未找到一手证据**」，不补脑补。
> 与既有报告的关系：`docs/research-pi-1.0.md`（基线 v1.0.4）[28]、`docs/DEEP_RESEARCH_pi-vs-langgraph-agent.md`（基线 v0.87.1）[29]。**本报告在 §0.3 列出与之冲突之处（已核实到 v1.2.0）。**

---

## TL;DR

1. **agent 的构造入口是「一个类 + 一个 options 对象」，没有工厂链、没有 profile 装配**：低层 `packages/agent` 只导出 `class Agent`，构造参数是扁平的 `AgentOptions`（`streamFn`、`convertToLlm`、`beforeToolCall`/`afterToolCall`、`finishTurn`、`prepareRequest`、`prepareNextTurn`、`initialState{systemPrompt,model,thinkingLevel,tools,messages}`…）；产品层再加一层 `createAgentSession(options)` 返回 `AgentSession` [4][5][21]。
2. **system prompt 是「分层 + 具名 section」，不是一整块字符串**：`packages/coding-agent/src/core/system-prompt.ts` 把提示拆成 `preamble / tools / rules / docs / addendum / project_context / skills / cwd` 等**具名 section**，除 `preamble` 外每个都包成 `<section_name>…</section_name>`；这些 section 存进 transcript 的 `SystemMessage.sections`，后续变更以 **section 补丁**（`null` = 删除）追加，而不是改写首条 system message [10][25]。
3. **loop 里没有「暂停等人」这个状态**：`runAgentLoop` 的出口只有「没有工具调用 + 没有排队消息 + 没有 follow-up」、`finishTurn` 返回 `{action:"end"}`、或 `stopReason` 是 `error`/`aborted` 三种。「等人」在 pi 里的实现是**一个阻塞的工具调用**（`question.ts` 的 `execute()` 里 `await ctx.ui.custom(...)`），或在非交互态直接返回错误文本——loop 本身照常往下走 [6][18]。
4. **「同一个 agent 在不同模式下只给一部分工具」在 pi 里由三层拼出来，核心没有任何 mode 对象**（本条是本次调研的要点）：
   - **模式 = 一串工具名**：`pi.setActiveTools(nameList)`，plan-mode / preset 扩展都只是「换名字列表」；
   - **`exposure`** = 每个工具自己的可达性等级（`direct`/`model-only`/`codemode`/`deferred`/`hidden`），决定它「是否声明给模型 / 是否可被别的工具调」；
   - **`prepareLoadout(loadout)`** = 编排型工具（如 `codemode`）在激活集变化时**重写已声明工具的描述、并把一部分工具从请求里隐藏掉**（`hiddenDeclarations`），自己承担向模型讲解它们的职责 [14][11][26]。
   **执行面由 `agent.state.tools` 决定，声明面由 transcript 的 system message 决定**，两者由 loop 的 `declareToolChanges()` 在每个请求前做差量对齐 [6][4]。
5. **工具面的变化是「追加一条 system message」，不是「改首条 prompt」**：`declareToolChanges()` 把 `context.tools` 与 transcript 已声明工具做 diff，产出 `toolsAdded`/`toolsRemoved` 的 system message；`hiddenDeclarations` 再由一个 `transformContext` 投影在**每次请求时**过滤掉——所以隐藏/激活状态能跨 `/tree` 与 resume 存活 [6][11]。
6. **plan 模式仍是 examples 扩展（`plan-mode/index.ts`），是「换 prompt + 换工具集 + 一个小状态」，不是独立状态机**：进入 = `setActiveTools(只读集)` + `before_agent_start` 注入隐藏消息 `[PLAN MODE ACTIVE]`；退出 = 用户在 `agent_end` 里选「Execute the plan」，把 todo 清单当**普通 follow-up 消息**送回同一条 transcript；进度靠对 assistant **文本**正则提取 `[DONE:n]`；resume 时重扫文本重建进度 [15][16]。
7. **pi 依旧没有内置逐调用审批**；审批是扩展的 `tool_call` 钩子，**确切签名是 `pi.on("tool_call", handler)`，event 是 `{type,toolCallId,parentToolCallId?,toolName,input}`，返回 `{block?,reason?,terminate?}`**；它挂在 `Agent.beforeToolCall` 上、由低层 loop 在**参数校验之后、执行之前**调用。**可以改参数**：`event.input` 与校验后的 args 是同一个对象引用，原地 mutate 即可，且**改完不再重新校验** [12][11][6][14]。
8. **1.0 那个「掏空内核」的动作在 v1.2.0 仍然成立**：`packages/agent/src` 只有 6 个文件（`agent.ts` / `agent-loop.ts` / `index.ts` / `proxy.ts` / `stream-fn.ts` / `types.ts`），CHANGELOG 的 1.0.0 Breaking Changes 原话是「now contains only `Agent`, the agent loop, the proxy stream, and their types」；1.1.0 只加了 `durationMs`，1.2.0 段为空。sessions/compaction/durable 全在 `@earendil-works/pi-durable`（自称 experimental）[8][9][24]。

---

## 0. 版本锚与取证边界

### 0.1 版本锚［置信度：High］

| 项 | 值 | 来源 |
|---|---|---|
| 最新 tag | `v1.2.0` | GitHub Releases / tags [1][2] |
| commit sha | `06a466935536f8c9bcacb2d5efc0fc021be1bdff` | `list_tags` / `get_tag` [2] |
| 发布日期 | `2026-10-12T23:03:53Z` | Releases API [1] |
| 默认分支 | `main` | repo metadata [3] |
| 许可 / stars | MIT / 114,951 | repo metadata [3] |
| 上一轮调研基线 | `v1.0.4`（`7c10bd43…`，2026-10-05） | [28] |
| 更早基线 | `v0.87.1`（`f07218c4…`，2026-09-22） | [29] |

tag 序列（近 5 个）：`v1.2.0` → `v1.1.0` → `v1.0.4` → `v1.0.3` → `v1.0.2` [2]。**因此本报告与两份既有报告之间存在一个未覆盖的版本区间（v1.0.4 → v1.2.0）**，§0.3 专列其差异。

### 0.2 取证边界

- 所有源码引用均固定到 `@v1.2.0`（路径形如 `packages/...`，链接为 `blob/v1.2.0/...`）。
- 行号只在**本轮实际读到行号的文件**里给出（`agent-session.ts`、`extensions/types.ts`、`extensions/runner.ts`、`codemode/tool.ts`、`subagent/index.ts`）；其余按**符号名**引用，避免伪造行号。
- 逐条结论末尾的「未找到一手证据」是**否定性结论**，不是省略。

### 0.3 与既有报告冲突／需修订之处［置信度：High］

| # | 既有说法 | 本轮核实（@v1.2.0） | 证据 |
|---|---|---|---|
| C1 | `plan-mode/index.ts` 与 subagent 等 example「字节级未变」（v0.87.1 → v1.0.4） | **对 subagent / plan-mode utils / permission-gate / structured-output 仍成立**（blob sha 相同）；但 **`plan-mode/index.ts` 已变**：`737ce56ac7` → `bbeb7ab137`，+268 字节 / +4 行 | contents API 逐 tag 取 sha [27] |
| C2 | — | 上述唯一改动是 `before_agent_start` 处理器从 `async () => {...}` 改为 `async (event) => {...}` 并加 `if (event.messageType !== "user") return;`，与 v1.2.0 的破坏性变更（`before_agent_start` 也开始对 custom-message run 触发）一致 | [15] + Release v1.2.0 body [1] |
| C3 | `DEEP_RESEARCH` 引作者原话「pi does not and will not support MCP」 | 1.0 起 **MCP 是内置扩展**（`builtin:mcp`），v1.2.0 的 MCP 面更大（`registerMcpServer`、`toolExposure`、`mcp_servers_change`） | [14][23]；[28] 已校正此条 |
| C4 | `DEEP_RESEARCH` 把 sessions/上下文描述为「一棵 JSONL 树」（在 coding-agent 内） | v1.2.0 里 **durable 运行时（`@earendil-works/pi-durable`）把 conversation/entry/task/submission 做成了独立包**；coding-agent 侧的 `SessionManager` 仍是 JSONL 树，是「SDK/CLI 形态」的那一份 | [24][21] |

---

## 1. 一个 agent 是怎么「装」起来的

### 1.1 低层构造入口：`class Agent` + `AgentOptions`［置信度：High］

`packages/agent`（包名 `@earendil-works/pi-agent-core`）的公开构造面就是**一个类**：

```ts
const agent = new Agent({
  initialState: { systemPrompt: "You are a helpful assistant.", model },
  streamFn: models.streamSimple.bind(models),
});
await agent.prompt("Hello!");
```

[7]（`packages/agent/README.md`「Quick Start」）[4]（`packages/agent/src/agent.ts` 的 `Agent` 类与 `AgentOptions`）

`AgentOptions` 的完整字段（源码 `agent.ts` 的 `interface AgentOptions`；README「Agent Options」同构）[4][7]：

| 分组 | 字段 | 说明 |
|---|---|---|
| 初始状态 | `initialState` | `{ systemPrompt, model, thinkingLevel, tools, messages }`，全部可选 |
| 模型调用 | `streamFn`（**当前 API 必填**）、`getApiKey`、`onPayload`、`onResponse`、`onProviderStreamEvent` | `streamFn` 缺省时构造器会回落到 `getDefaultStreamFn()` |
| 消息转换 | `convertToLlm`（必填语义）、`transformContext` | 前者 `AgentMessage[] → Message[]`，后者在转 LLM 之前做修剪/注入 |
| 工具执行 | `beforeToolCall`、`afterToolCall`、`toolExecution`（`"parallel"` 默认 / `"sequential"`） | |
| 回合控制 | `finishTurn`、`prepareRequest`、`prepareNextTurn` / `prepareNextTurnWithContext` | |
| 排队 | `steeringMode`、`followUpMode`（默认均 `"one-at-a-time"`）、`steer()` / `followUp()` | |
| 其它 | `sessionId`、`thinkingBudgets`、`transport`、`maxRetryDelayMs` | `sessionId` 转发给 provider 用于 prompt-cache 亲和 |

**没有必需的「工具注册表」参数**：工具是通过 `agent.state.tools = [...]`（可写访问器）在运行时装配的。`initialState.tools` 只是一个初始值 [4][7]。

**必需 vs 可选的一手判据**：`AgentOptions` 里只有 `streamFn` 在类型上必填；源码注释还专门写了一句「Older compiled consumers may omit options or streamFn even though the current API requires them」，即构造器对两者都做了兜底 [4]。

### 1.2 system prompt 怎么组装：**分层 + 具名 section**［置信度：High］

这是本次调研里信息量最大的一块。分两级看：

**（a）agent 内核只做一件事**：`systemPrompt` + 工具声明 → 合成**首条 system message**。

```ts
const initialMessage = createInitialSystemMessage(initialState?.systemPrompt, tools.map(toToolDeclaration));
if (messages[0]?.role !== "system" && initialMessage) messages.unshift(initialMessage);
```

[4]（`createMutableAgentState`）。也就是说内核层的「system prompt」= 一段字符串 + 工具声明表，写进 transcript 头。

**（b）真正的分层组装在 coding-agent**：`packages/coding-agent/src/core/system-prompt.ts` 的 `buildSystemPromptSections()` 产出一张**有序、具名的 section 表**，键名固定、值可独立替换 [10]：

| section | 内容 | 来源 |
|---|---|---|
| `preamble` | 基础指令。默认文案是 `"You are an expert coding assistant operating inside pi, a coding agent harness. …"`；**`customPrompt`（= `SYSTEM.md`）会整段替换它** | [10] |
| `tools` | `- <name>: <snippet>` 的工具清单，仅列 `selectedTools` 中**未被 hidden** 且有 snippet 的 | [10] |
| `rules` | 工具条件规则（如「有 bash 但没 grep/find/ls → 用 bash 做文件操作」）+ 各工具 `promptGuidelines` + `promptGuidelines` 追加项 + 两条固定项（简洁、路径清晰）；去重 | [10] |
| `docs` | pi 自身文档/示例的路径与「什么时候读它们」的指引 | [10] |
| `addendum` | `appendSystemPrompt`（= `APPEND_SYSTEM.md`） | [10] |
| `project_context` | 上下文文件，每条包成 `<project_instructions path="…">…</project_instructions>` | [10] |
| `skills` | skill 描述（`formatSkillsForPrompt`）。**细节有趣**：若文件读取工具被 hidden，则 hint 里不点名任何工具，改用 `"indirect"` 语义 | [10] |
| `cwd` | 工作目录（反斜杠转 `/`） | [10] |
| 自定义 | `sections: Record<string,string>`，键名受 `^[a-z][a-z0-9_-]*$` 约束且禁用 `preamble` | [10] |

除 `preamble` 外，每个 section 被包成 `<name>\n…\n</name>`；注释明写目的：**「so the model can match later updates to it」** [10]。

**（c）section 是「追加补丁」而非重写**：`SystemMessage.sections` 的定义是「The leading message declares them; later messages replace sections by name, and `null` removes one」，并提醒「Avoid integer-like names; JSON objects reorder those」[25]。coding-agent 侧由 `diffSystemPromptSections(previous, current)` 计算最小补丁，产出 `{role:"system", content:"", sections: patch}` [10][11]。

### 1.3 注入点清单［置信度：High］

| 注入点 | 形态 | 位置 |
|---|---|---|
| `SYSTEM.md` | **替换**默认 prompt 前缀（`customPrompt`） | `<agent-dir>/SYSTEM.md`、`.pi/SYSTEM.md`；项目文件优先于 agent 目录，同名不合并 [22][10] |
| `APPEND_SYSTEM.md` | **追加**到 prompt（`appendSystemPrompt` → section `addendum`） | 同上 [22] |
| `AGENTS.md` / `CLAUDE.md` / `AGENTS.override.md` | 上下文文件 → section `project_context`；从 agent 目录、工作目录及**其父目录**发现 | [22] |
| 扩展 `sections` | 任意 XML section（`<name>…</name>`） | `buildSystemPromptOptions.sections` [10] |
| `toolSnippets` / `toolGuidelines` | 逐工具的一行说明与规则条目 | `registerTool` 的 `promptSnippet` / `promptGuidelines` [12][10] |
| `before_agent_start` 返回 `systemPrompt` | **替换该 run 的整份 prompt**；transcript 仍记录结构化 section，替换文本只在请求上「投影」 | [12][11] |
| 扩展 `context` / `context_with_system` | 请求级改写整段转录（`context` 看不到 system message，Pi 事后还原其状态） | [14] |

> **未找到一手证据**：pi 里**没有**「instructions 文件的优先级继承链」这种东西——`AGENTS.override.md` **只替换同目录**的 `AGENTS.md`/`CLAUDE.md`，「It does not suppress context files from the agent directory or other directories」[22]。这与「就近覆盖、逐层合并」的直觉不同。

### 1.4 产品层构造入口：`createAgentSession()`［置信度：High］

```ts
const { session } = await createAgentSession();   // 用 cwd、发现的资源、存储的 settings、配置好的凭据
await session.prompt("…");                        // run 结束后 resolve
session.dispose();
```

[21]（`docs/sdk.md`「Session lifecycle」）[30]（`packages/coding-agent/src/core/sdk.ts`）

`AgentSession` **拥有**：一个 conversation、model、tools、排队消息、compaction 状态、extension runtime [21]。可显式替换的边界（`CreateAgentSessionOptions`）：`modelRuntime` / `model` / `thinkingLevel` / `scopedModels`、`settingsManager`、`sessionManager`、`resourceLoader`、`tools` / `noTools` / `excludeTools` / `customTools` [30][21]。

`tools` 的语义有一处反直觉之处，源码注释写得最清楚：**「一串只由 `+name` / `-name` 组成的列表不是 allowlist，而是对默认集的增删」**；混用会抛错 [30]。

---

## 2. agent loop 的形状

### 2.1 入口与出口［置信度：High］

```ts
export function agentLoop(prompts, context, config, signal, streamFn): EventStream<AgentEvent, AgentMessage[]>
export function agentLoopContinue(context, config, signal, streamFn): EventStream<AgentEvent, AgentMessage[]>
export async function runAgentLoop(prompts, context, config, emit, signal, streamFn): Promise<AgentMessage[]>
export async function runAgentLoopContinue(context, config, emit, signal, streamFn): Promise<AgentMessage[]>
```

[6]（`packages/agent/src/agent-loop.ts`）。`Agent.prompt()` → `runAgentLoop`，`Agent.continue()` → `runAgentLoopContinue` [4]。

事件序列（README 给出完整用例）[7]：

```
agent_start
└ turn_start
  ├ message_start/end { userMessage }
  ├ message_start … message_update … message_end { assistantMessage }
  ├ tool_execution_start / _update / _end
  ├ message_start/end { toolResultMessage }
  └ turn_end { message, toolResults }
└ (下一轮 turn_start … 或)
agent_end
```

**一轮（turn）= 一次 assistant 响应 + 它触发的工具调用与结果** [6][7][20]。

### 2.2 一轮什么时候结束：五种出口［置信度：High］

`runLoop` 是双层 `while (true)`（外层管 follow-up，内层管工具与 steering）。出口逐条 [6]：

| 出口 | 条件 | 行为 |
|---|---|---|
| E1 自然结束 | 内层 `hasMoreToolCalls === false` 且无 pending/steering，外层无 follow-up，且无 `explicitContinuation` | `break` → `agent_end` |
| E2 显式收尾 | `finishTurn` 返回 `{ action: "end" }` | 发 `turn_end` 后立即 `agent_end`，**不再轮询队列** [5][7] |
| E3 硬退出 | assistant `stopReason` 为 `"error"` 或 `"aborted"` | 发 `turn_end` + `agent_end` 返回；`finishTurn` 的决定被忽略 [6][7] |
| E4 续跑 | `finishTurn` 返回 `{ action: "continue" }` | **保证一次**下一次 provider 请求（若工具结果/steering/follow-up 已经会造成请求，则不额外发）[5] |
| E5 follow-up | 外层发现 `getFollowUpMessages()` 非空 | 回到内层再跑 [6] |

注意：**pi 的 loop 照旧没有 `max_steps`**（上一轮报告已记录作者原话，本轮未复核）[28]。

`finishTurn` 的语义在 README 里有明确警示：「`returning { action: "continue" }` unconditionally creates an endless loop」[7]。

### 2.3 「停下来等人」：**loop 里没有这个概念**［置信度：High］

这是本题最关键的一条。pi 在三个层面**分别**处理「等人」，但**没有任何一个层面是 loop 的挂起/恢复状态**：

**（a）工具执行前的拦截 ≠ 暂停**。`beforeToolCall` 返回 `{block:true, reason}` 时，loop 的 `prepareToolCall` 立刻把它变成一个 **error tool result** 并**继续下一轮**（模型读到 `reason`）[6][5]：

```ts
if (beforeResult?.block) {
  const result = createErrorToolResult(beforeResult.reason || "Tool execution was blocked");
  if (beforeResult.terminate === true) result.terminate = true;
  return { kind: "immediate", result, isError: true };
}
```

**（b）真正的「等人」是一个阻塞的工具调用**。`examples/extensions/question.ts` 注册了一个 `question` 工具，它的 `execute()` 里 `await ctx.ui.custom(...)`，用户作答后把答案作为 tool result 返回；**并且它对非交互态有明确分支** [18]：

```ts
if (ctx.mode !== "tui") {
  return { content: [{ type: "text", text: "Error: UI not available (running in non-interactive mode)" }], … };
}
```

也就是说：**「等人」= 当前 turn 里一个不返回的 tool call**。暂停发生在工具内部，loop 只是「在等一个 tool result」。

**（c）跨 turn 的「停下/继续」靠 session + abort，不靠 loop 状态**：`abort()` 中止当前 run 并把排队消息还给编辑器 [20]；`agent_end` 只是「本次低层 run 结束」，**`agent_settled` 才是「pi 不会再自动继续」**，SDK 文档明说它是主机该等的信号 [21][14]；恢复对话是**重开 session**（JSONL 树的 active leaf），不是 loop 的 resume [20][21]。

> **源码实证小结**：`AgentEvent` 联合类型里没有 `paused` / `awaiting_input` / `resumed` 之类的事件 [5]；`AgentState` 里也没有「等待判决」字段 [5]。
> **推断**：pi 里对应 langgraph-agent「确认闸门三档终态」的东西，只有「block → 错误结果」与「工具内部阻塞」两种，**且都没有「超时未答」这第三态的位置**（`timed-confirm.ts` 的 timeout 语义是「当作否」，见既有报告 [28] §5.3）。

---

## 3. 工具面：pi 怎么表达「同一个 agent 在不同模式下只给一部分工具」

> 这是本次调研最要紧的一题。**结论：核心没有 mode 概念；模式由三层叠加表达——`setActiveTools` 的名字列表 + `exposure` 的可达性等级 + `prepareLoadout` 的声明投影。** 下面按「注册 → 可达 → 声明 → 生效」的顺序拆。

### 3.1 注册 API 与 schema 来源［置信度：High］

```ts
pi.registerTool({
  name, label, description,          // label = UI，description = 模型可见
  promptSnippet?, promptGuidelines?, // 进 system prompt 的 tools / rules section
  parameters: Type.Object({…}),      // TypeBox schema
  outputSchema?,                     // structuredContent 的 schema
  exposure?, namespace?, annotations?, reachesTools?, defaultActive?,
  prepareLoadout?, executionMode?,
  execute(toolCallId, params, signal, onUpdate, ctx) { … },
  renderCall?, renderResult?,
});
```

[12]（`ToolDefinition`，`packages/coding-agent/src/core/extensions/types.ts`）。

- **schema 来源是 TypeBox**（`import { Type } from "typebox"`），低层用 `validateToolArguments(tool, preparedToolCall)` 校验；工具还可选 `prepareArguments` 做「校验前的兼容垫片」[4]（`AgentTool`）、[6]（`prepareToolCall`）。
- **`execute()` 是 5 参**，第 5 个是 `ExtensionToolContext`——它比 `ExtensionContext` 多两样：`tools`（`executeTool` 能调的集合）与 `executeTool()` 本身 [12]。

### 3.2 `exposure`：五档可达性［置信度：High］

| exposure | 声明给模型？ | 可被 `ctx.executeTool()` 调？ | 注册时自动激活 |
|---|---|---|---|
| `direct`（默认） | 激活时声明 | 激活时可调 | 是 |
| `model-only` | 激活时声明 | **永不** | 是 |
| `codemode` | 显式激活才声明 | **注册即可调**，且 codemode 会列出它 | 否 |
| `deferred` | 显式激活才声明 | 注册即可调，但 codemode **不列**它；`tool_search` 能找到并激活 | 否 |
| `hidden` | 否 | 否 | 否（**工具无法注销，撤回 = 用 `hidden` 重注册**） |

[14]（`docs/extensions.md`「Tool exposure」）[12]（`ToolExposure` 类型，L514）

源码判据在 `agent-session.ts`（行号为实际读到）：

```
L1566-1572  _getCallableTools()  =  active 的 direct ∪ 全部 codemode ∪ 全部 deferred
L1535-1545  _isAllowedTool()     =  --tools / --exclude-tools 的过滤
L1546-1558  _isActivatable()     =  hidden 不可激活；MCP 工具在 allowlist 未匹配时只有非 direct 且 tool_search 在场才可激活
```

[11]

### 3.3 激活集 = 「声明给模型的那一组工具」，且它只是**名字列表**［置信度：High］

```ts
/** Set the active tools by name. Unknown and `hidden` tools are ignored. … */
setActiveTools(toolNames: string[]): void;
getActiveTools(): string[];
getAllTools(): ToolInfo[];   // 每个工具带 exposure / namespace / annotations / reachesTools
```

[12]（`ExtensionAPI` 方法）[14]

**关键否证**：我在整个 v1.2.0 里**没有找到**任何「mode registry / mode 对象 / 模式枚举」这一等概念——**「模式」在 pi 里就是一个 `string[]`**。证据是三个官方 example 的做法完全一致：

| example | 换模式时做的事 |
|---|---|
| `plan-mode/index.ts` | `pi.setActiveTools(getPlanModeTools(toolsBeforePlanMode))` / `pi.setActiveTools(toolsBeforePlanMode ?? getNormalModeTools(...))` [15] |
| `preset.ts` | `"tools": ["read","grep","find","ls"]` → 过滤掉未注册名后 `pi.setActiveTools(validTools)`，并快照 `originalState.tools` 以便还原 [17] |
| `codemode`（settings） | `codemode.mode: "on" \| "only"`，`"only"` 时「active built-in and extension tools are hidden from the model, so it reaches them through `codemode`」 [23] |

> `preset.ts` 是本次调研的意外收获：它是「plan 预设 / implement 预设」这种**模式即配置**的最直白样本——`presets.json` 里每个预设含 `{provider, model, thinkingLevel, tools, instructions}`，切换时同时换模型、换 thinking level、换工具集，并把 `instructions` 用 `before_agent_start` 返回的 `systemPrompt` 拼到 prompt 尾部 [17]。**这就是 pi 对「同一个 agent 在不同模式下」给出的最完整产品形态。**

### 3.4 `prepareLoadout()`：让一个工具**代理整张工具面**［置信度：High］

这是 1.0 引入、且本次最值得记的机制。

```ts
prepareLoadout?: (loadout: ToolLoadout) => ToolLoadoutChanges | undefined;

interface ToolLoadout {
  declared: readonly AgentTool[];     // 激活（= 声明给模型）的工具，带原始描述
  callable: readonly AgentTool[];     // 可被 ctx.executeTool() 调的
  registered: readonly AgentTool[];   // 全部已注册
  getExposure(name): ToolExposure;
  getNamespace(name): ToolNamespace | undefined;
  getPromptGuidelines(name): readonly string[];
}
interface ToolLoadoutChanges {
  descriptions?: Record<string, string>;      // 替换已声明工具（含自己）的模型可见描述
  hiddenDeclarations?: readonly string[];     // 激活但「请求里不带声明」的工具
}
```

[12]（`ToolLoadout` / `ToolLoadoutChanges` / `prepareLoadout`）[14]（语义说明）

调用点与效果，逐行对应源码：

```
L1586-1598  _applyToolLoadout(): 按名字取工具 → 过滤 hidden / 不可激活 → 收集带 prepareLoadout 的 hook
L1599-1618    构造 loadout（declared / callable / registered / getExposure / getNamespace / getPromptGuidelines）
              逐个调用 hook，累积 descriptions 与 hiddenDeclarations；hook 抛错走扩展错误上报，不中断
L1618-1625    declared = tools.map(替换 description)；this._hiddenDeclarations = hidden;
              this.agent.state.tools = declared        ← 执行面
L1769-1786  _installHiddenDeclarationsProjection(): 包一层 transformContext，
              把每条 system message 的 toolsAdded/toolsRemoved 里被 hidden 的条目**过滤掉**  ← 声明面
L1704-1729  _rebuildSystemPrompt(): 把 hiddenTools 传给 buildSystemPromptSections()
              → tools section 与 rules section 都不列它们
```

[11]（行号为实际读到）[14]（「The default system prompt leaves hidden tools out of its tool list and rules, and names no tool in the skills hint when the file reader is hidden, so the orchestrating tool should present their guidelines itself」）

**官方把 `codemode` 当作这个 hook 的唯一使用者的存在性证明**：

```ts
exposure: "model-only",
prepareLoadout: (loadout) => prepareCodemodeLoadout(loadout, options),
reachesTools: (input) => getScriptReach(input.code),
```

[26]（`packages/coding-agent/src/extensions/codemode/tool.ts`）。该文件注释还说明了**为什么按 exposure 而不是按 active set 列表**：

> Listing by exposure, not by the active set, keeps the codemode description unchanged when `tool_search` loads a tool, so loads do not redeclare codemode. [26]

即：**让模型可见的工具清单尽量稳定，避免工具声明抖动砸掉 prompt cache**。

### 3.5 声明面怎么跟执行面对齐：`declareToolChanges()`［置信度：High］

低层 loop 每个请求前调一次：

```
context.tools （可执行的）  vs  transcript 已声明的工具
  → getToolStateChanges(...) → { toolsAdded, toolsRemoved }
  → 若已有一条 pending system message 就并入它，否则插到第一条非 system 消息之前
```

[6]（`declareToolChanges`）。函数注释同时点明了**两个面各自的语义**：

> `context.tools` is what the runtime can execute; the transcript's system messages declare what the model may call.

以及**为什么必须做成差量**：

> When a pending system message exists, its tool fields are treated as intent and replaced with the delta between the committed transcript and the executable set, so replay always yields exactly `context.tools`.

[6]

`SystemMessage` 的字段因此长这样：`toolsAdded?: Tool[]`、`toolsRemoved?: ToolReference[]` [25]。

### 3.6 `ctx.executeTool()`：嵌套调用［置信度：High］

```ts
ctx.executeTool(name, args, options?: ExecuteToolOptions): Promise<AgentToolCallOutcome>
```

[12]（`ExtensionToolContext`）[14]。要点 [14][5][6]：

- 嵌套调用**照走参数校验与 `tool_call` / `tool_result` handler**（所以审批钩子对嵌套调用同样生效）；
- 事件带 `parentToolCallId`，`toolCallId` 形如 `<parent id>/<n>`；
- **不产生 transcript 条目**，只在调用者结果上留**有界记录** `nestedCalls`（name/arguments/status/duration/error，**永不存结果**；>8 KiB 参数 / >32 KiB 结果省略，最多 256 条，`complete:false` 标记有丢失）；
- 嵌套结果的 `usage` **逐层加总到调用者结果上**（所以「一个工具只报自己的 usage，不报它调用的那些」的账目能算平）。
- 低层实现是 `runToolCall(toolCall, options)`，源码注释明说「Tools that call other tools use this so the hooks (for example permission checks) apply to those calls too」[6]。

### 3.7 tool renderer［置信度：High］

- 工具自带 `renderCall(args, theme, context)` / `renderResult(result, options, theme, context)` [12]；
- 全局起底：`pi.registerToolRenderer((toolName, next) => renderers)` 能为**尚未注册**的工具（如 resume 时 server 还没连上的 MCP 工具）指定渲染；`next()` 返回「后续 resolver → 已注册工具」会用的渲染器，所以 `next() ?? mine` 只做兜底 [14]。

### 3.8 v1.0.4 → v1.2.0 的增量［置信度：High］

| 项 | 变化 | 证据 |
|---|---|---|
| `reachesTools` | **新增**（v1.2.0）：工具声明「一次调用可能触及哪些工具」（`"all"` 或输入里的词），MCP 扩展用它等服务器连上、并决定替换的 codemode/tool_search 能否触及 MCP 工具 | Release v1.2.0「Added」[1]；`ToolDefinition.reachesTools` [12]；`codemode/tool.ts` [26] |
| `namespace.aliases` | **新增**：codemode 的 `describeNamespace()` 与 `searchTools({namespace})` 会匹配别名（如 `docs` 匹配 `mcp__docs`） | [1][12][14] |
| `codemode` 声明增强 | 工具声明里出现输入约束（integer / min / max / 默认值） | [1] |
| `before_agent_start` | **破坏性**：也对 `pi.sendMessage(..., {triggerTurn:true})` 起的 run 触发，event 变成按 `messageType` 的联合（`"user"` 带 `prompt`/`images`，`"custom"` 带 `message`） | [1][12] |

---

## 4. plan 模式到底怎么实现的（`examples/extensions/plan-mode/index.ts` 逐段）

> 该文件在 v1.0.4 与 v1.2.0 之间有 **+268 字节**改动（§0.3 C1）。下面**按 v1.2.0** 逐段说明。

### 4.1 文件顶部的自述与常量［置信度：High］

```
* Read-only exploration mode for safe code analysis.
* When enabled, built-in write tools are disabled.
* Features: /plan 命令或 Ctrl+Alt+P 切换；bash 限制为只读白名单；从 "Plan:" 段提取编号步骤；
*           [DONE:n] 标记完成步骤；执行期进度 widget
```

```ts
const PLAN_MODE_TOOLS = ["read", "bash", "grep", "find", "ls", "questionnaire"];
const NORMAL_MODE_TOOLS = ["read", "bash", "edit", "write"];
const PLAN_MODE_DISABLED_TOOLS = new Set(["edit", "write"]);
```

[15]。**第一眼就能看出结论：这是「换 prompt + 换工具集」，工具集就是一个字符串数组。**

### 4.2 状态：**四个模块级变量 + 一条自定义 session 条目**［置信度：High］

```ts
let planModeEnabled = false;
let executionMode = false;
let todoItems: TodoItem[] = [];
let toolsBeforePlanMode: string[] | undefined;
```

[15]。持久化用 `pi.appendEntry("plan-mode", {enabled, todos, executing, toolsBeforePlanMode})`，在 `togglePlanMode()` / `turn_end` / `agent_end` 里调用；`session_start` 时读回并恢复 [15]。

> **要点**：**没有独立状态机**。这是「4 个闭包变量 + 一条 append-only 的自定义条目」。三态（normal / plan / executing）由两个布尔组合表达。

### 4.3 进入与退出［置信度：High］

| 动作 | 触发 | 实现 |
|---|---|---|
| 进入 plan 模式 | `/plan` 命令、`Ctrl+Alt+P`、或 `--plan` flag | `enablePlanModeTools()`：`toolsBeforePlanMode ??= pi.getActiveTools()`，然后 `pi.setActiveTools(getPlanModeTools(toolsBeforePlanMode))`——**过滤掉 `edit`/`write`，并入 `read/bash/grep/find/ls/questionnaire`** [15] |
| 退出 plan 模式 | 再次 toggle | `restoreNormalModeTools()`：`pi.setActiveTools(toolsBeforePlanMode ?? getNormalModeTools(...))`，并清空 `toolsBeforePlanMode` [15] |
| 进入 executing | `agent_end` 里 `ctx.ui.select("Plan mode - what next?", [...])` 选「Execute the plan」 | `planModeEnabled=false; executionMode=true; restoreNormalModeTools();` 然后 sendMessage（见 4.5）[15] |
| executing 结束 | `agent_end` 里发现 `todoItems.every(t => t.completed)` | 发一条 `plan-complete` 消息，清空 `executionMode`/`todoItems`，**并 `persistState()` 把清空后的状态存下来**（注释：`Save cleared state so resume doesn't restore old execution mode`）[15] |

### 4.4 三个事件钩子（这是「模式」的全部机制）［置信度：High］

**（a）`tool_call`：只读白名单闸门**——限制 `bash`，不满足就 block（**注意：这是 block，不是暂停**）：

```ts
pi.on("tool_call", async (event) => {
  if (!planModeEnabled || event.toolName !== "bash") return;
  const command = event.input.command as string;
  if (!isSafeCommand(command)) {
    return { block: true, reason: `Plan mode: command blocked (not allowlisted). …` };
  }
});
```

[15]。`isSafeCommand` 在 `utils.ts`：`SAFE_PATTERNS`（`cat/head/grep/find/ls/git status|log|diff…`）命中**且** `DESTRUCTIVE_PATTERNS`（`rm/mv/chmod/sudo/git commit|push|…`）不命中 [16]。

**（b）`before_agent_start`：注入模式指令（`display:false` 的隐藏 custom 消息）**：

```ts
pi.on("before_agent_start", async (event) => {
  if (event.messageType !== "user") return;      // ← v1.2.0 新增的唯一改动
  if (planModeEnabled) {
    return { message: { customType: "plan-mode-context", display: false,
      content: `[PLAN MODE ACTIVE]\n… Create a detailed numbered plan under a "Plan:" header:\n…` } };
  }
  if (executionMode && todoItems.length > 0) {
    return { message: { customType: "plan-execution-context", display: false,
      content: `[EXECUTING PLAN - Full tool access enabled]\n\nRemaining steps:\n…\nAfter completing a step, include a [DONE:n] tag in your response.` } };
  }
});
```

[15]。**指令是「隐藏消息」，不是「换 system prompt」**（`display:false` 只影响 UI；它作为 custom message 进入 transcript 并参与模型上下文）。

**（c）`context`：反向清理过期指令**——不在 plan 模式时，把残留的 `plan-mode-context` 消息与含 `[PLAN MODE ACTIVE]` 的用户消息过滤掉：

```ts
pi.on("context", async (event) => {
  if (planModeEnabled) return;
  return { messages: event.messages.filter((m) => { … customType === "plan-mode-context" → false; … }) };
});
```

[15]

**（d）`turn_end`：进度靠正则读 assistant 文本**：

```ts
const text = getTextContent(event.message);
if (markCompletedSteps(text, todoItems) > 0) updateStatus(ctx);
```

`markCompletedSteps` → `extractDoneSteps` → `/\[DONE:(\d+)\]/gi` [15][16]。

### 4.5 退出后怎么把计划交给下一步：**当普通 follow-up 消息送回同一条 transcript**［置信度：High］

```ts
pi.sendMessage(planTodoListMessage, { deliverAs: "followUp" });
pi.sendMessage(
  { customType: "plan-mode-execute", content: execMessage, display: true },
  { triggerTurn: true, deliverAs: "followUp" },
);
```

其中 `execMessage = \`Execute the plan.\n\nRemaining steps:\n${remainingList}\n\nStart with: ${firstTodoItem.text}\nAfter completing a step, include a [DONE:n] tag in your response.\`` [15]。

复用「Refine the plan」分支则走 `pi.sendUserMessage(refinement, { deliverAs: "followUp" })` [15]。

> **这就是「计划如何进入执行上下文」的答案：它不进入任何旁路状态——它就是对话本身。** `codemode` 之外唯一「带外」的东西是那条 `plan-mode-execute` 条目，而它只用来当 resume 重扫的**起始锚点**。

### 4.6 todo 从哪来：从 assistant 文本里**正则提取**［置信度：High］

`extractTodoItems(message)`：先找 `Plan:` 头（`/\*{0,2}Plan:\*{0,2}\s*\n/i`），再在后续文本里匹配 `/^\s*(\d+)[.)]\s+\*{0,2}([^*\n]+)/gm`，过滤长度 ≤5、以反引号/斜杠/短横开头的项，`cleanStepText()` 去掉加粗/代码/动词前缀并截到 50 字符 [16]。

### 4.7 resume 时怎么重建进度［置信度：High］

```ts
// Only scan messages AFTER the last "plan-mode-execute" to avoid picking up [DONE:n] from previous plans
let executeIndex = -1;  // 从后往前找最后一条 customType === "plan-mode-execute"
// 收集 executeIndex 之后所有 type === "message" 的 assistant 消息 → 拼成一整段文本
markCompletedSteps(allText, todoItems);
```

[15]。**代价写在注释里**：进度不是状态字段，而是「重放文本」。若没有那条 `plan-mode-execute` 锚点（`executeIndex` 一直是 -1），重扫会覆盖到更早的内容。

### 4.8 一句话形态总结［置信度：High］

| 维度 | plan 模式的实现 |
|---|---|
| 是独立状态机吗 | **不是**。4 个闭包变量 + `pi.appendEntry("plan-mode", …)` [15] |
| 是「换 prompt」吗 | **是，但走隐藏消息**（`before_agent_start` 返回 `display:false` 的 custom message），不是换 system prompt [15] |
| 是「换工具集」吗 | **是**：`pi.setActiveTools(...)`，一个字符串数组 [15] |
| 有独立 LLM 调用链 / 第二 agent 吗 | **没有**。全程同一 session、同一 transcript [15][20] |
| 怎么进入 / 退出 | 命令 / 快捷键 / flag；退出时把 todo 清单当 follow-up 消息送回 [15] |
| 怎么跟踪进度 | 对 assistant 文本正则提取 `[DONE:n]`；resume 靠重扫文本 [15][16] |

---

## 5. 审批 / 拦截机制

### 5.1 结论：pi 未内置逐调用审批［置信度：High］

`security.md` 的原话（上一轮已引，本轮未复核）：「it **does not ask for approval before every tool call**」；project trust 只管资源加载 [28] §5.1。本轮在 v1.2.0 的 `extensions.md` 里找到的也是**配方**而非内置闸门 [14]。

### 5.2 `tool_call` 钩子的**确切签名与返回形状**［置信度：High］

```ts
// 注册
on(event: "tool_call", handler: ExtensionHandler<ToolCallEvent, ToolCallEventResult>): () => void;

interface ToolCallEventBase {
  type: "tool_call";
  toolCallId: string;
  parentToolCallId?: string;   // 由别的工具（如 codemode 脚本）发起时才有
}
// 内置工具各有窄化版本：BashToolCallEvent { toolName: "bash"; input: BashToolInput } …
interface CustomToolCallEvent extends ToolCallEventBase { toolName: string; input: Record<string, unknown>; }
export type ToolCallEvent = BashToolCallEvent | … | CustomToolCallEvent;

interface ToolCallEventResult {
  block?: boolean;
  reason?: string;
  terminate?: boolean;   // 整批工具结果都为 true 时才提前结束自动跟进
}
```

[12]。事件文档原话（这半句是本条的关键）：

> Fired before a tool executes. Can block. **`event.input` is mutable. Mutate it in place to patch tool arguments before execution. Later `tool_call` handlers see earlier mutations. No re-validation is performed after mutation.**

[12]

### 5.3 在哪一层被调用：**低层 loop 的 `beforeToolCall`，在参数校验之后、执行之前**［置信度：High］

调用链（每一跳都有源码）：

```
agent-session.ts L653  this.agent.beforeToolCall = (context) => this._beforeToolCall(context);
        L659-681      _beforeToolCall({toolCall, args}) → runner.emitToolCall({
                        type:"tool_call", toolName, toolCallId, …, input: args as Record<string, unknown>
                      })                                   ← args 与校验后的参数是同一引用
        runner.ts     L1249 emitToolCall(event)：按扩展加载顺序跑 handler，
                        任一 handler 返回 { block: true } 就立刻 return（短路）
agent-loop.ts          prepareToolCall()：validateToolArguments → config.beforeToolCall(...) → 若 block 则 immediate error result
```

[11][13][6]

**因此「能不能改参数」的答案是：能，但只有一条路**——原地 mutate `event.input`（= 校验后的 args 对象），因为 `beforeToolCall` 的返回值类型 `BeforeToolCallResult` 里**只有** `block` / `reason` / `terminate`，**没有 `args` 字段** [5][12]。而且**改完不会重新校验** [12]。

**handler 抛错的行为**（两处一手证据，语义一致）：

- 文档：`tool_call` handler failure **blocks the tool as a fail-safe** [14]；
- 源码：`agent-session.ts` 的 `_beforeToolCall` catch 分支把非 Error 的抛出重包为 `new Error(\`Extension failed, blocking execution: ${String(err)}\`)` [11]。

### 5.4 现成的「类 Codex 审批」配方［置信度：High］

官方给出的基于 MCP 注解的写法（注解缺失时取 MCP 默认：非只读、可能破坏、可达开放世界）[14]：

```ts
pi.on("tool_call", async (event, ctx) => {
  const hints = pi.getAllTools().find((tool) => tool.name === event.toolName)?.annotations;
  const needsApproval =
    hints?.destructiveHint === true ||
    (!hints?.readOnlyHint && ((hints?.destructiveHint ?? true) || (hints?.openWorldHint ?? true)));
  if (needsApproval && !(await ctx.ui.confirm("Allow tool call?", event.toolName))) {
    return { block: true, reason: `${event.toolName} was not approved` };
  }
});
```

**注意这仍然是「否 = block → 错误结果 → 模型继续」**，不是「挂起等人判决」。

> **未找到一手证据**：v1.2.0 的内置代码里**没有**任何「工具执行前弹确认」的路径（`permission-gate.ts` 仍是 example，且 `!ctx.hasUI` 时默认 block [28] §5.2）。
> **推断**：pi 的 `tool_call` 钩子**有位置**（执行前、可改参数、可 block）但没有**状态**（没有 pending-approval 的持久位）。langgraph-agent 的三档终态（拒绝 / 超时未答 / 停止打断）在 pi 侧没有对应物；若要与 pi 对齐做跨 agent 闸门评测，得自己用扩展把第三态造出来。

---

## 6. 会话与状态

### 6.1 1.0 的「最小内核」落实核验［置信度：High］

`packages/agent` 的 Breaking Changes 原话：

> Removed the experimental harness from `@earendil-works/pi-agent-core`: `AgentHarness`, sessions and session storage, the durable runtime, pico3, harness tools, compaction, skills, prompt templates, system prompt helpers, telemetry schemas, the search service types, and the `uuidv7` and pi-telemetry re-exports. … **The package now contains only `Agent`, the agent loop, the proxy stream, and their types.** Use `@earendil-works/pi-durable` for durable sessions.

[8]

**目录实证**（v1.2.0 `packages/agent/src` 仅 6 个文件）[9]：

| 文件 | 对应「只剩什么」 |
|---|---|
| `agent.ts` | `Agent`（有状态包装） |
| `agent-loop.ts` | agent loop |
| `proxy.ts` | proxy stream（README 的 `streamProxy`） |
| `stream-fn.ts` | 默认 stream 函数 |
| `types.ts` | 上述的类型 |
| `index.ts` | 出口 |

CHANGELOG 后续版本对内核的改动极小：**1.1.0 只加了 `durationMs`**（工具结果消息 / `AgentToolCallOutcome` / `tool_execution_end`，用单调时钟、不含 hooks），**1.2.0 段为空** [8]。

### 6.2 谁持有消息 / session / transcript［置信度：High］

分两层，**职责边界很清楚**：

**（a）进程内（`packages/agent`）**——`Agent` 持有 `MutableAgentState`，其中 `messages` 就是 transcript；`state.systemPrompt` 是**只读**的「从 transcript 的 system messages 重放出来的当前 prompt」（要改 prompt 就追加一条带 `content` 或 `sections` 的 system message）[4][5]。

**（b）产品服务层（coding-agent）**——`SessionManager`：

> `SessionManager` owns the persisted or in-memory entry tree and tracks its active leaf. … **`SessionManager` is authoritative for finalized model context.** Restore external history by constructing the session with a manager containing those entries. **Assigning `session.agent.state.messages` does not replace persisted context.**

[21]

持久化格式是 JSONL（v3）树：每条 entry 有 `id` / `parentId`，**截止到当前 entry 的那条分支就是活跃分支**，为下一次模型请求提供历史 [20][21]。

**（c）durable 运行时（`@earendil-works/pi-durable`，自称 experimental）** [24]：

> **Experimental.** The API changes without notice between releases.

概念表（原话摘录）：

| 概念 | 定义 |
|---|---|
| **Harness** | 一份打开的 storage + 在其上跑 agent 的机器；所有变更走一条原子 commit 线，**没提交的不展示** |
| **Conversation** | 一份 transcript；`root()` 首次使用时创建；handle 无状态，按 `id` 比较 |
| **Entry** | 一条不可变记录：`pi.user` / `pi.assistant` / `pi.tool-result` / `pi.system` / `pi.reset` / 自定义。**模型只看到最近一次 reset 之后的条目** |
| **Commit** | 原子写；可在一次 commit 里同时追加 entry、改 document、建 task |
| **Document** | 存在 transcript 旁边的类型化 JSON 状态：`pi.agent`（每对话的 agent 选择）、`pi.provider`（provider 侧 session 身份）、`pi.live`（在跑的 generation/tool）、`pi.inbox`（排队提交）、`pi.usage`（花费） |
| **Task** | 每步存 checkpoint 的持久状态机；有 owner（它所属的 conversation 或另一个 task）。built-in task：`pi.generation`（调模型、拥有其工具调用的 `pi.tool` task、等它们、把 run 交给下一个 generation） |
| **Submission** | 交给 conversation 的东西（用户输入或一条 entry），可 `wait()` |
| **Turn / Run** | turn = 一次模型响应 + 其工具调用；run = 从一次输入到最终答案之间的若干 turn。**一个 conversation 在 run 期间是 busy** |
| **Agent** | conversation 运行所用的：model、thinking level、选中的 extensions、tools、instructions、cwd。**按名字存在 `pi.agent` 里，每次使用时对着 registry 解析** |

[24]

### 6.3 多轮对话怎么续（对「一个任务 = 一段多轮对话」的参照）［置信度：High］

| 层面 | 机制 |
|---|---|
| 低层 | `agent.prompt()` 新起一个 run；`agent.continue()` 从现有 transcript 续（要求队尾是 user/toolResult）；`steer()` / `followUp()` 把消息塞进队列 [4][7] |
| 产品层（SDK） | `session.prompt()` 在 run 结束后 resolve；run 中再 prompt **必须**声明 steer 还是 follow-up，否则拒绝（不猜）[21] |
| 持久层 | JSONL 树 + active leaf；`SessionManager` 重建模型上下文（选活跃分支 + 应用 compaction）[21] |
| durable 层 | Conversation + Entry + Submission；`submit(input) → pi.user → pi.generation → pi.assistant(tool calls) → pi.tool × n / pi.tool-result × n → pi.generation → pi.assistant(answer) → submission done` [24] |
| 终止信号 | `agent_end`（一次低层 run 结束，自动重试/恢复/排队可能还在）vs **`agent_settled`（pi 不会再自动继续）** [21][14] |

> **对「一个任务 = 一段多轮对话」的直接参照**：pi 把「任务」放在 durable 的 **Task** 概念上，而把「对话」放在 **Conversation** 上，两者是**不同层**——`pi.generation` 这个 task 拥有它的 `pi.tool` task，工具结果作为 entry 落回**同一个 conversation**。也就是说 pi 的答案是「**一段对话 + 若干挂在它上面的 task**」，而不是「一个任务 = 一个 agent 实例」[24]。

---

## 7. 子 agent

### 7.1 形态：spawn 独立 `pi` 进程，JSON 模式读事件［置信度：High］

顶部注释（`examples/extensions/subagent/index.ts`）：

> Spawns a separate `pi` process for each subagent invocation, giving it an isolated context window. Supports three modes: Single / Parallel / Chain. Uses JSON mode to capture structured output from subagents.

[19]。本次逐段核实的形状（行号为实际读到）：

**启动参数（L300-347）**：

```ts
const args: string[] = ["--mode", "json", "-p", "--no-session"];
if (model) args.push("--model", model);
if (inheritsDispatchConfig && dispatchDefaults.thinkingLevel) args.push("--thinking", dispatchDefaults.thinkingLevel);
if (agent.tools?.length) args.push("--tools", agent.tools.join(","));
// agent 自带 systemPrompt 时：写到临时文件，然后
args.push("--append-system-prompt", tmpPromptPath);
args.push(`Task: ${task}`);
spawn(invocation.command, invocation.args, { cwd: cwd ?? defaultCwd, shell: false, stdio: ["ignore","pipe","pipe"] });
```

[19]。`finally` 里 `unlinkSync(tmpPromptPath)` + `rmdirSync(tmpPromptDir)` [19]。

> 「上下文隔离」= **进程隔离 + `--no-session`**；子代理默认看不到父对话（父只传 task 字符串、可选 systemPrompt 临时文件、可选工具面）。

**父子之间怎么传消息**：**只有 stdout 的一条条 JSON 事件**（一行一个 JSON 对象，解析失败就 `return` 跳过）：

```ts
if (event.type === "message_end" && event.message) { … }
if (event.type === "tool_result_end" && event.message) { … }
```

[19]。

**怎么回填结果**（这是「结果回填」的完整字段清单）：

- 每次 `message_end` 累积进 `currentResult.messages`；assistant 消息上累计 `usage.turns / input / output / cacheRead / cacheWrite / cost.total / contextTokens`，并记录首个 `model`、`stopReason`、`errorMessage` [19]；
- 收尾得到：

```ts
interface SingleResult {
  agent; agentSource: "user"|"project"|"unknown"; task;
  exitCode: number; messages: Message[]; stderr: string;
  usage: {input,output,cacheRead,cacheWrite,cost,contextTokens,turns};
  model?; stopReason?; errorMessage?; step?;
}
interface SubagentDetails {
  mode: "single"|"parallel"|"chain";
  agentScope; projectAgentsDir: string|null;
  results: SingleResult[];
}
```

[19]（L149-167）——`details` 是**全量机器可读结果**，进 `tool_execution_end.result.details`。

**并发与中止**：`MAX_PARALLEL_TASKS = 8`、`MAX_CONCURRENCY = 4`、`PER_TASK_OUTPUT_CAP = 50 * 1024`；超上限**直接返回错误**而非截断执行；中止时 `signal` → `SIGTERM`，5s 后 `SIGKILL`，并抛 `"Subagent was aborted"` [19]。

**观测性在渲染层**：`renderResult` 逐条画子代理的 assistant text 与 toolCall，折叠时显示前 5 条 + `(Ctrl+O to expand)`；展开时按 `Step n:` 分段，最后打 `formatUsageStats()`（`N turns / ↑ / ↓ / R / W / $ / ctx`）与**聚合 total** [19]。

---

## 8. 对 langgraph-agent 的启发

> **立场声明：本节全部是推断（推断标记），不是 pi 的一手陈述。** 每条前面挂了我据以推断的实证位置。

### 8.1 可直接借的形态（推断）

**P1.「模式」不该是一等对象，该是「一份可快照/还原的声明集」。**（据 §3.3）
pi 里模式就是 `string[]`（工具名），切换时先快照 `pi.getActiveTools()` 再设新集合，退出时还原 [15][17]；`preset.ts` 甚至把「模式」外置成 JSON（`{provider, model, thinkingLevel, tools, instructions}`）[17]。启发：**模式 = 一份可序列化的声明（工具面 + prompt 增量 + 模型/思考档），而不是在节点里写 `if mode == "plan"`。** 这样模式能被 UI 切换、能被持久化、能被 resume 还原。

**P2.「声明面」与「执行面」分开，并用差量对齐。**（据 §3.5）
pi 的 `context.tools`（能执行）与 transcript 的 system message（声明给模型）是两个面，`declareToolChanges()` 每请求前算 `toolsAdded/toolsRemoved` 差量，且**做成追加而不是改写** [6]。启发：如果本仓库要支持「同一 agent 不同工具子集」，**把「模型见过的工具集」当成 transcript 里的一条可回溯记录**，而不是一个会被覆盖的配置字段——它能天然解决「resume/分支切换后工具面错位」。

**P3. `prepareLoadout` 这个「编排者代理整张工具面」的位置值得单独留出来。**（据 §3.4）
`codemode` 只用了 `exposure` + `reachesTools` + `ctx.executeTool()` + `prepareLoadout` 四样东西，就把「N 个工具收成一个工具 + 一段提案式描述」做完了，且**另一张同名工具可以复刻**它 [14][26]。启发：本仓库若要「一个工具面按需展开」，需要的不是更多工具，而是**一个「活跃集变化时会被回调、能改写描述与隐藏声明」的 hook 位**。

**P4. 工具面变化要尽量不动「模型可见前缀」。**（据 §3.4、§3.5）
`prepareCodemodeLoadout` 的注释直说「Listing by exposure, not by the active set, keeps the codemode description unchanged when tool_search loads a tool, so loads do not redeclare codemode」[26]；`declareToolChanges` 也把变化做成追加的 system message [6]。启发：**能力热插拔与 prompt cache 是同一笔账**；若本仓库要引入「能力包按需激活」，把「会变的清单」从稳定前缀里挪出去、改为追加，是已被 pi 定价过的做法（代价也已被 pi 写明：不能表达这种迁移的 provider 会收到完整 checkpoint [14]）。

**P5. 计划/子任务的产出应当作为「普通消息」回到同一条会话。**（据 §4.5、§7.1）
plan 模式把 todo 清单当 `deliverAs:"followUp"` 的用户消息送回同一 transcript [15]；子代理则反过来——**用进程隔离 + 把子进程的 `messages`/`usage`/`stopReason` 全量回填进父级 tool `details`** [19]。启发：这是两个相反的极端，中间地带（子任务需要带着父上下文续跑）pi **没有提供**，得自己设计注入通道 [19]。

### 8.2 需要自行验证的权衡（推断）

**Q1. pi 没有「暂停等待判决」的第三态。**（据 §2.3、§5）
`tool_call` 的 block 是「拒绝」，`question.ts` 的等待是「工具内部阻塞且非交互态直接报错」 [18]，`timed-confirm` 的 timeout 是「视为否」[28]。**若本仓库的确认闸门要做跨 agent 对照，pi 侧没有可直接对齐的形态**；相反，pi 把「非交互态」的语义定成了「拒绝 + 让模型看见原因」。

**Q2. plan 模式的进度是文本约定，不是状态字段。**（据 §4.6、§4.7）
`[DONE:n]` 从 assistant 文本正则提取；resume 时**重放文本**重建，且要靠一条 `plan-mode-execute` 锚点限定扫描范围 [15][16]。若照抄这条形态，必须把「进度从哪里重建、锚点丢失怎么办」当独立问题回答。

**Q3. 模式切换如果伴随工具集切换，就落进 pi 明码标价的 cache 代价里。**（据 §3.5）
`extensions.md` 原话：「Providers that cannot represent the transition receive a complete transcript checkpoint, which can invalidate the cached prefix」[14]。还有一条来自 `preset.ts` 的暗示：preset 同时换 model + thinkingLevel + tools + instructions [17]，而 pi 在别处明说**跨 turn 换模型会丢一次 prompt cache**（见 [28] §9.2 T6）。**「模式切换换不换模型、代价记在哪笔账」必须自己回答。**

**Q4. durable 包自称 experimental，而「会话即任务图」这套概念恰好都在它身上。**（据 §6.2）
`packages/durable/README.md` 开头即 `**Experimental.** The API changes without notice between releases.` [24]。若要借鉴 conversation/entry/task/submission 这套所有权模型（task 拥有 task、run 期间 conversation busy、模型只看最近 reset 之后的 entry），得接受 API 会变。

**Q5. pi 的工具注册面重度依赖 TypeBox schema。**（据 §3.1）
`registerTool` 的 `parameters` 是 TypeBox，校验走 `validateToolArguments` [12][4]。若本仓库要复用「工具定义即声明」这套形态，需要先确认本仓库的 schema 方案（`write`/`edit` 等工具的 `registers_artifact` 一类语义在 pi 侧由 `annotations` 近似承担 [14]）。

---

## 来源清单

编号与正文引用一一对应。全部为 Tier 1（GitHub 源码 @ 固定 tag / blob sha，或仓库内官方 docs）。

### 版本、发布与仓库元数据

| # | 标题 | URL | 日期 |
|---|---|---|---|
| 1 | Release **v1.2.0**（`reachesTools` / `namespace.aliases` / codemode 声明约束 / `before_agent_start` 破坏性变更 / per-key OpenRouter 列表 / 配置 schema） | https://github.com/earendil-works/pi/releases/tag/v1.2.0 | 2026-10-12T23:03:53Z |
| 2 | `list_tags` / `get_tag`：`v1.2.0` = `06a466935536f8c9bcacb2d5efc0fc021be1bdff`；tag 序列 v1.2.0 / v1.1.0 / v1.0.4 / v1.0.3 / v1.0.2 | https://github.com/earendil-works/pi/tags | 2026-10-14 查询 |
| 3 | repo metadata：`default_branch=main`、`license=MIT`、`stargazers_count=114951` | https://github.com/earendil-works/pi | 2026-10-14 查询 |

### `packages/agent`（最小内核）

| # | 标题 | URL |
|---|---|---|
| 4 | `packages/agent/src/agent.ts` @ v1.2.0：`class Agent`、`AgentOptions`、`createMutableAgentState`（`createInitialSystemMessage` + unshift）、`prompt/continue/steer/followUp/abort/waitForIdle/reset`、`processEvents` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/agent/src/agent.ts |
| 5 | `packages/agent/src/types.ts` @ v1.2.0：`BeforeToolCallResult` / `AfterToolCallResult` / `BeforeToolCallContext`、`AgentState`（只读 `systemPrompt`）、`AgentTool`、`AgentEvent` 联合、`ToolExecutionMode` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/agent/src/types.ts |
| 6 | `packages/agent/src/agent-loop.ts` @ v1.2.0：`runAgentLoop` / `runLoop` 双层 while、`declareToolChanges` + `getToolStateChanges`、`prepareToolCall`（validate → `beforeToolCall` → block）、`executeToolCalls{Sequential,Parallel}`、`runToolCall`（嵌套调用走同一批 hooks） | https://github.com/earendil-works/pi/blob/v1.2.0/packages/agent/src/agent-loop.ts |
| 7 | `packages/agent/README.md` @ v1.2.0：Quick Start、事件序列、`beforeToolCall`/`afterToolCall`/`finishTurn`/`prepareRequest` 语义与警告、Agent Options 全表、`continue()` 队列行为、transcript 拥有 prompt 与工具声明、`declareToolChanges` 说明 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/agent/README.md |
| 8 | `packages/agent/CHANGELOG.md` @ v1.2.0：`[1.0.0]` Breaking Changes 原文；`[1.1.0]` 仅加 `durationMs`；`[1.2.0]` 段为空；`[0.87.0]` 移除 `shouldStopAfterTurn` 改 `finishTurn` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/agent/CHANGELOG.md |
| 9 | `packages/agent/src` 目录清单 @ v1.2.0：`agent.ts` / `agent-loop.ts` / `index.ts` / `proxy.ts` / `stream-fn.ts` / `types.ts`（6 个文件） | https://github.com/earendil-works/pi/tree/v1.2.0/packages/agent/src |

### prompt 组装与 session 运行时

| # | 标题 | URL |
|---|---|---|
| 10 | `packages/coding-agent/src/core/system-prompt.ts` @ v1.2.0：`BuildSystemPromptOptions`、`buildSystemPromptSections`（`preamble`/`tools`/`rules`/`docs`/`addendum`/`project_context`/`skills`/`cwd` + 自定义 section）、`<name>` 包裹、`buildSystemPromptState`（`forceSystemPrompt`）、`diffSystemPromptSections` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/core/system-prompt.ts |
| 11 | `packages/coding-agent/src/core/agent-session.ts` @ v1.2.0：L475 `_hiddenDeclarations`；L653 `agent.beforeToolCall` 接线；L659 `_beforeToolCall`（`input: args`）；L1474 systemPrompt getter；L1522 `setActiveToolsByName`；L1566 `_getCallableTools`；L1586 `_applyToolLoadout`（`prepareLoadout` 调用）；L1704 `_rebuildSystemPrompt`；L1746 `_preparePromptAndToolLoadout`；L1769 `_installHiddenDeclarationsProjection` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/core/agent-session.ts |
| 12 | `packages/coding-agent/src/core/extensions/types.ts` @ v1.2.0：L514 `ToolExposure`；L376 `ExtensionToolContext.executeTool`；L551 `ToolLoadout` / `ToolLoadoutChanges`；L579-668 `ToolDefinition`（`exposure`/`namespace`/`reachesTools`/`annotations`/`defaultActive`/`prepareLoadout`/`execute(5 参)`/`renderCall`/`renderResult`）；L1193-1259 `ToolCallEvent`；L1454 `ToolCallEventResult`；L1766 `getActiveTools`/`getAllTools`/`setActiveTools`；L1662 `on("tool_call", …)` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/core/extensions/types.ts |
| 13 | `packages/coding-agent/src/core/extensions/runner.ts` @ v1.2.0：L1249 `emitToolCall()`（按加载顺序、`{block:true}` 短路）、`emitContext` 两阶段（`context` / `context_with_system`） | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/core/extensions/runner.ts |
| 30 | `packages/coding-agent/src/core/sdk.ts` @ v1.2.0：`createAgentSession()`、`CreateAgentSessionOptions`（`tools`/`noTools`/`excludeTools`/`customTools`/`sessionManager`/`settingsManager`/`resourceLoader`）、`+name`/`-name` 语义 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/core/sdk.ts |

### 官方文档

| # | 标题 | URL |
|---|---|---|
| 14 | `packages/coding-agent/docs/extensions.md` @ v1.2.0：Events and concurrency、Tools（`ctx.executeTool` / `nestedCalls`）、**Tool exposure**、**prepareLoadout / reachesTools**、Activate tools dynamically、Tool rendering、MCP servers、Codex 式审批配方、`tool_call` fail-safe 语义 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/extensions.md |
| 20 | `packages/coding-agent/docs/how-pi-works.md` @ v1.2.0：Agent loop / Context / Sessions / Interfaces / Extensions / Trust 六节 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/how-pi-works.md |
| 21 | `packages/coding-agent/docs/sdk.md` @ v1.2.0：Session lifecycle、SessionManager 权威性与「assigning `agent.state.messages` does not replace persisted context」、Prompting（steer vs follow-up 不猜）、`agent_settled`、Configuring a session | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/sdk.md |
| 22 | `packages/coding-agent/docs/configuration.md` @ v1.2.0：agent 目录与 `.pi/` 的资源表（`SYSTEM.md` / `APPEND_SYSTEM.md` / `AGENTS.md`）、Context files 与 `AGENTS.override.md` 的「只替换同目录」语义 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/configuration.md |
| 23 | `packages/coding-agent/docs/settings.md` @ v1.2.0：`defaultTools` 语义、`codemode.mode: "on"\|"only"`、`codemode.inlineBudget`、内置扩展命名（`builtin:mcp` 等） | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/settings.md |
| 31 | `packages/coding-agent/docs/sessions.md` @ v1.2.0（continue/branch/compaction/storage/export） | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/sessions.md |

### examples 与 durable

| # | 标题 | URL |
|---|---|---|
| 15 | `packages/coding-agent/examples/extensions/plan-mode/index.ts` @ v1.2.0：常量、4 个闭包变量、`pi.registerFlag("plan")`、`/plan` 命令、`Ctrl+Alt+P`、`tool_call` 只读白名单、`context` 反向清理、`before_agent_start`（含 v1.2.0 新增的 `messageType` 守卫）、`turn_end` 文本进度、`agent_end` 选「Execute the plan」+ `deliverAs:"followUp"`、`session_start` 恢复与重扫 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/examples/extensions/plan-mode/index.ts |
| 16 | `packages/coding-agent/examples/extensions/plan-mode/utils.ts` @ v1.2.0：`DESTRUCTIVE_PATTERNS` / `SAFE_PATTERNS`、`isSafeCommand`、`extractTodoItems`、`extractDoneSteps`、`markCompletedSteps`、`cleanStepText` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/examples/extensions/plan-mode/utils.ts |
| 17 | `packages/coding-agent/examples/extensions/preset.ts` @ v1.2.0：`presets.json`（`{provider, model, thinkingLevel, tools, instructions}`）、`applyPreset` 快照/还原、`--preset` / `/preset` / `Ctrl+Shift+U`、`before_agent_start` 拼接 instructions、`appendEntry("preset-state")` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/examples/extensions/preset.ts |
| 18 | `packages/coding-agent/examples/extensions/question.ts` @ v1.2.0：`question` 工具的 `execute()` 内 `await ctx.ui.custom(...)`；`ctx.mode !== "tui"` 时返回错误文本；`executionMode: "sequential"` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/examples/extensions/question.ts |
| 19 | `packages/coding-agent/examples/extensions/subagent/index.ts` @ v1.2.0：L33-36 常量、L149-167 `SingleResult` / `SubagentDetails`、L300-347 spawn 参数与 tmp systemPrompt、L355-400 JSONL 事件累积 usage、L405-425 中止与清理、`TaskItem`/`ChainItem`/`SubagentParams`、渲染层 usage 聚合 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/examples/extensions/subagent/index.ts |
| 24 | `packages/durable/README.md` @ v1.2.0：开头的 `**Experimental.**`；Concepts（Harness/Conversation/Entry/Commit/Document/Task/Submission/Turn and run/Extension/Registry/**Agent**）；Persist and Resume；`submit → pi.generation → pi.tool × n → pi.generation` 时序 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/durable/README.md |
| 26 | `packages/coding-agent/src/extensions/codemode/tool.ts` @ v1.2.0：`prepareCodemodeLoadout`、`exposure: "model-only"`、`reachesTools: (input) => getScriptReach(input.code)`、`constrainedSampling` grammar、与 `loadout.getExposure` 的交互 | https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/src/extensions/codemode/tool.ts |

### pi-ai 类型与跨版本比对

| # | 标题 | URL |
|---|---|---|
| 25 | `packages/ai/src/types.ts` @ v1.2.0：`SystemMessage`（`content` / `sections` / `toolsAdded` / `toolsRemoved` 的语义与「replay 出当前 prompt 与工具」）、`Context.systemPrompt` 与 `normalizeContext` | https://github.com/earendil-works/pi/blob/v1.2.0/packages/ai/src/types.ts |
| 27 | blob sha 逐 tag 比对（contents API `?ref=v1.0.4` vs `?ref=v1.2.0`）：`plan-mode/index.ts` `737ce56ac7` → `bbeb7ab137`；`plan-mode/utils.ts` / `subagent/index.ts` / `permission-gate.ts` / `structured-output.ts` **逐字节相同** | https://github.com/earendil-works/pi/tree/v1.2.0/packages/coding-agent/examples/extensions | 

### 本仓库既有报告（Tier 2，仅用于交叉引用与冲突标注）

| # | 标题 | URL |
|---|---|---|
| 28 | `docs/research-pi-1.0.md`（基线 v1.0.4）：§1.4 无 semver 政策、§2.2 plan mode 机制、§2.4 blob sha 对比、§5 安全/审批、§9 启发 | 本仓库 `docs/research-pi-1.0.md` |
| 29 | `docs/DEEP_RESEARCH_pi-vs-langgraph-agent.md`（基线 v0.87.1）：§2 骨架、§3 拒绝清单 | 本仓库 `docs/DEEP_RESEARCH_pi-vs-langgraph-agent.md` |
