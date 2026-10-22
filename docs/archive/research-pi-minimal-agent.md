# pi 的最小 agent 到底有多小（源码级）

> **基线**：本地克隆 `E:\code\pi`，HEAD `4dd2b149d155d7369de16ea4dc77c84e9f5ffb24`，`git describe --tags` = `v1.3.0-10-g4dd2b149d`（2026-10-17 读取）。
> **方法**：只读源码 + 仓库内文档；行号均属该 commit。版本区间用 `git diff --stat` 核对；否定性结论写明检索范围与命中数（见 §4 表头），不靠记忆补。
> **边界**：`未核实` = 仓库里查不到一手依据，本报告不做推断式补全。本报告只回答「最小 agent 是什么样」，不评价产品好坏。
>
> **2026-10-19 注**：该克隆此后已更新到 `482c1381`（多 20 个提交）；[决策日志](../capability-registry-rescope.md) §0.5 引用的 pi 行号属 `4dd2b149d`，与本文同基线，引用前仍以各自基线复核。

## 一句话结论

pi 的最小 agent = **一个 `while` 循环 + 一份可写状态（messages / tools / model）+ 一个外部注入的 `StreamFn` + 一组可选 hook**。
它**不包含**任何工具实现、持久化、压缩、审批、模式、MCP——这些全部落在宿主 `packages/coding-agent` 或扩展里。最小核只负责「把消息发给模型、执行模型点名的工具、决定还转不转」。

## 1. 不可约核心

### 1.1 尺寸：核就是 6 个文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `packages/agent/src/agent-loop.ts` | 949 | 循环、工具执行、事件发射 |
| `packages/agent/src/agent.ts` | 613 | 有状态封装、队列、事件归约 |
| `packages/agent/src/types.ts` | 539 | 全部类型（状态/消息/工具/事件/hook） |
| `packages/agent/src/proxy.ts` | 393 | 可选：浏览器代理转发 |
| `packages/agent/src/stream-fn.ts` | 20 | 默认 streamFn 的插槽 |
| `packages/agent/src/index.ts` | 5 | 只 re-export 上述模块 |

依赖只有两个：`@earendil-works/pi-ai` 与 `typebox`（`packages/agent/package.json:25-28`）。`index.ts:1-5` 的导出面里没有任何工具、会话、压缩符号；除 `proxy.ts:147` 一处 `fetch` 外，核不碰文件系统/进程/网络。

### 1.2 五个不可约要素

| 要素 | 落点 | 具体形态 |
|---|---|---|
| **循环** | `agent-loop.ts:38-61`（`agentLoop`）、`71-100`（`agentLoopContinue`）、`163-321`（`runLoop`） | 外层 `while (true)`（`:179`）等 follow-up；内层 `while (hasMoreToolCalls \|\| pendingMessages.length > 0)`（`:183`）跑 turn |
| **状态** | `types.ts:382-421`（`AgentState`） | 只有 `systemPrompt / model / thinkingLevel / tools / messages / isStreaming / streamingMessage / pendingToolCalls / errorMessage`；`tools`、`messages` 是 getter/setter，赋值时浅拷贝（`agent.ts:94-105`） |
| **消息** | `types.ts:374`（`AgentMessage`）、`502-507`（`AgentContext`）、`222`（`convertToLlm`）、`244`（`transformContext`） | 循环全程用 `AgentMessage`，只在调 LLM 前一次性转 `Message[]`（`agent-loop.ts:394-397`）。`convertToLlm` 是**必填**契约，`transformContext` 可选，注释直接点名「做裁剪/压缩」（`types.ts:225-244`） |
| **工具调用** | `types.ts:466-499`（`AgentTool`）、`agent-loop.ts:508-523`（顺/并行）、`708-777`（校验 + 前置 hook）、`859-910`（后置 hook） | 工具就是一个 `execute(id, args, signal, onUpdate)`；参数按 TypeBox schema 校验（`:727`），失败/未知工具都变成 `isError` 结果，不抛穿循环 |
| **停止条件** | `agent-loop.ts:245-256`、`286-292`、`296-318` | 四种出口：① 模型 `error`/`aborted` 硬退出；② `finishTurn` 返回 `{action:"end"}`；③ 没有工具调用 + 没有 steering + 没有 follow-up（结构性退出，`296-317`）；④ 队列空则 `break` |

**事件面**只有 10 种（`types.ts:516-539`：`agent_start / agent_end / turn_start / turn_end / message_start / message_update / message_end / tool_execution_start / tool_execution_update / tool_execution_end`），订阅是一个 `Set`（`agent.ts:190`、`266-269`）。**turn 的定义**是「一次 assistant 响应 + 它的工具结果」（`types.ts:520-522`，事件序列见 `packages/agent/README.md:83-107`）。

**一个容易被忽略的取舍**：循环本身不改 `Agent.state`，它只改自己的 context 副本（`agent-loop.ts:112-115`）并 emit 事件；状态由 `Agent.processEvents` 从事件归约（`agent.ts:565-603`，`message_end` 才 `push` 进 messages，`:577`）。loop 因此可以脱离 `Agent` 单独用（`agent.ts` 与 `agent-loop.ts` 是两个导出层，`index.ts:1-2`）。

## 2. 内置 / 宿主 / 扩展的边界

| 能力 | 落点 | 证据 |
|---|---|---|
| 工具**实现** | 宿主内置 8 个：`read/bash/powershell/edit/write/grep/find/ls` | `coding-agent/src/core/tools/index.ts:95-105`、`164-211`。核里 0 个（`packages/agent/src` 检索 `createReadTool\|createBashTool\|createEditTool` = 0 命中） |
| 工具**注册** | 宿主一张 `Map<name, ToolDefinition>` + 一张 `Map<name, AgentTool>` | `agent-session.ts:473`（`_toolRegistry`）、`479`（`_toolDefinitions`）；注册口在 `extensions/loader.ts:289-301` |
| 事件订阅 | 核：一个 `Set`；宿主：扇出到扩展 + 会话监听；扩展：`pi.on(...)` | 核 `agent.ts:266-269`；宿主 `agent-session.ts:1144-1145`；扩展 `extensions/types.ts:1592-1664`（32 个 `on(event:` 重载） |
| LLM 包装 | 核只声明 `StreamFn` 形状并留一个默认插槽；宿主注入 `streamSimple` 并加超时/重试/headers | `types.ts:33-37`；`stream-fn.ts:11-19`；`sdk.ts:46`、`420-431`、`348-365` |
| 会话持久化 | **纯宿主**：`message_end` → `appendMessage`；文件是 append-only JSONL 树 | `agent-session.ts:1148-1168`；`session-manager.ts:977`。核里无 fs（见 §1.1 检索） |
| 压缩 | **纯宿主**：阈值检查 + 在 `prepareNextTurn` 里压缩后换 context | `agent-session.ts:777-786`、`899-933`、`1894-1896`；核只提供 `transformContext` 这个空位（`types.ts:244`） |
| plan mode | **example 扩展**，不是产品功能 | `packages/coding-agent/README.md:19`（"skips features like sub-agents and plan mode"）；`examples/extensions/plan-mode/index.ts:108`（`setActiveTools`）、`203`（`before_agent_start` 注入） |
| 权限 / 审批 | 核只有 `beforeToolCall` 钩子；宿主把它接到扩展 `tool_call`；**无内置审批** | `types.ts:326`；`agent-loop.ts:728-756`；`agent-session.ts:653-681`；`docs/security.md:3` |
| MCP | **内置扩展**，把每个 MCP 工具变成 `AgentTool` | `extensions/mcp/index.ts:444`（`pi.registerTool`）；`extensions/types.ts:1894`（`registerMcpServer`）；`packages/agent/README.md:508` |
| 子代理 | 不发货（example 形态） | `packages/coding-agent/README.md:19`；具体 spawn 参数本轮未重读，标 `未核实` |

**一句话**：核里只有「循环 + 状态 + 注入点」；**工具面、持久化、压缩、模式、审批、MCP 一个都不在核里**。

## 3. 扩展面到底有多大

### 3.1 扩展能贡献什么

一个扩展 = 一个默认导出工厂，参数是 `ExtensionAPI`（`docs/extensions.md:15`，`extensions/types.ts:2045`）：

| 类别 | 接口 | 证据 |
|---|---|---|
| 工具 | `registerTool(tool: ToolDefinition)` | `types.ts:1670-1673`；实现 `loader.ts:289-301` |
| 事件 | `on(event, handler)`，32 个 `on(event:` 重载 | `types.ts:1597-1664` |
| 命令 / 快捷键 / CLI flag | `registerCommand` / `registerShortcut` / `registerFlag` | `types.ts:1680-1708` |
| 模型供应商 | `registerProvider(name, config)` | `types.ts:1858-1859` |
| MCP 服务器 | `registerMcpServer(name, config)` | `types.ts:1894` |
| 渲染 | `registerMessageRenderer` / `registerEntryRenderer` / `registerToolRenderer` / `registerMarkdownTransformer` | `types.ts:1714-1724` |
| 会话动作 | `sendMessage` / `sendUserMessage` / `appendEntry` / `setActiveTools` / `getAllTools` | `types.ts:1730-1790`、`1747`、`1778` |
| 工具内嵌套调用 | 工具 `execute()` 的第 5 个参数 `ctx.executeTool()` | `types.ts:383-395` |
| 上下文 / provider 请求改写 | `context`、`before_provider_request`、`before_provider_headers` | `types.ts:885-914`；接线在 `sdk.ts:382-409`、`436-440` |

### 3.2 扩展不能贡献什么

- **不能加循环、加节点、换调度**。`AgentOptions` 全是 hook，没有「替换 loop」的入口（`agent.ts:114-141`）；`finishTurn` 只能 `end` / `continue`（`types.ts:147`、`264`）。扩展侧最接近的权限是**请求再来一轮**（`turn_end` / `agent_before_settle` 的 `continue`，`types.ts:1015-1025`、`1065-1072`），而不是定义一条新控制流。
- **不能给核加消息角色**。`AgentMessage` 是 `Message | CustomAgentMessages[...]`，扩展点是**编译期**声明合并（`types.ts:365-374`）；运行时宿主只给一个 `role: "custom"`（`core/messages.ts:46-47`）。
- **不能注销工具**。仓库文档明说「tools cannot be unregistered」，撤回要靠 `exposure: "hidden"` 重注册（`docs/extensions.md:161`；`coding-agent/src` 检索 `unregisterTool|removeTool` = 0 命中）。
- **只能往固定的条目类型里写**。扩展能追加的会话条目是一个闭集：`CustomEntryDraft | CustomMessageEntryDraft | ContextEditEntryDraft | CompactionEntryDraft`（`types.ts:1001-1005`）。
- **不能把 agent 挂起等人**。核里检索 `pause|suspend|awaiting` = 0 命中；「问人」的做法是**一个阻塞的工具调用**（`examples/extensions/question.ts:52` 的 `execute` 里 `await ctx.ui.custom(...)`，`:73`）。

## 4. 刻意的极简：哪些是「有意不做」

| 取舍 | 类型 | 证据 |
|---|---|---|
| 没有逐调用审批闸门 | **有意**：文档直说 | `docs/security.md:3`「it does not ask for approval before every tool call」；`agent-session.ts` 工具路径检索 `confirm\|approval` = 0 命中 |
| 没有内置沙箱，权限=OS 账号权限 | **有意**：文档直说，并给隔离方案 | `docs/security.md:3-7`、`19`；`docs/how-pi-works.md:49` |
| 内核不带会话/压缩/技能（1.0 起） | **有意**：1.0 Breaking Changes 写明「now contains only Agent, the agent loop, the proxy stream」 | `packages/agent/CHANGELOG.md` 的 `[1.0.0] / Breaking Changes` |
| 不带 plan mode / 子代理 | **有意**：README 明说 skip | `packages/coding-agent/README.md:19` |
| 工具不可注销 | **有意**：文档解释并给替代 | `docs/extensions.md:161` |
| 没有 runner / run manifest / 件清单 | **无此概念**（未找到一手依据说这是决策） | `packages/agent/src` + `packages/coding-agent/src` 检索 `manifest\|run_manifest` = 0 命中 |
| 没有图节点 / 状态机框架 | **无此概念**：就是一个 `while` | `agent-loop.ts:163-321`；`coding-agent/src` 检索 `StateGraph\|graph node` = 0 命中 |
| 没有步数/turn 上限 | **只是没做**：全仓 0 命中，也没有文档解释 | `packages/agent/src` + `coding-agent/src` 检索 `maxTurns\|maxSteps\|maxIterations` = 0 命中；唯一出口是 §1.2 的四种结构性条件 |
| 状态里没有「暂停/待批准/待输入」 | **只是没做**：形态上被「阻塞工具」替代（§3.2） | 核检索 `pause\|suspend\|awaiting` = 0 命中 |
| 工具声明不在配置里，而在 transcript 里 | **有意**：有专门函数做差量 | `agent-loop.ts:333-363`（`declareToolChanges`）：把 `context.tools` 与 transcript 已声明工具求差，差量写成一条 system message |

整张表里最值得记住的不是「缺了什么」，而是最后一行：**工具声明不在配置里，而在 transcript 里**。核只维护「能执行什么」（`AgentState.tools` 数组，`types.ts:400-401`），「告诉模型有什么」由 `declareToolChanges` 从 transcript 反推（`agent-loop.ts:333-363`）；模式切换（`plan-mode/index.ts:108`）只是换那个数组。

## 5. v1.3.0-10 相对两份既有调研：最小 agent 没有实质变化

只列有源码证据的差异，`git diff` 全部在本地 clone 上跑：

| 区间 | agent 包的实际 diff | 结论 |
|---|---|---|
| `v1.0.4..v1.2.0` | `agent-loop.ts` +11 / `types.ts` +12 / `proxy.ts` -21 行，内容只有 `durationMs`（`agent-loop.ts:678`、`841`、`926`、`941`；`types.ts:453-454`、`531-539`） | 对应 1.1.0 changelog 唯一一条「Added `durationMs`」；`research-pi-agent-construction.md` 已记，无新变化 |
| `v1.2.0..HEAD`（含 v1.3.0） | `packages/agent` 只有 `CHANGELOG.md` + `package.json`（版本号）；`src/` 零改动 | **最小 agent 语义完全未变**；1.3.0 的 agent changelog 段为空 |
| 同期宿主侧 | `agent-session.ts` 15 行：恢复会话时按 transcript 记录的 loadout 还原激活工具，再叠 `+name/-name`（`agent-session.ts:526`、`1809-1825`） | 这是 1.3.0「Resumed sessions keep their active tools」那条修复，方向是**加强**「transcript = 声明权威」，不是改边界 |
| plan mode | `v1.2.0..HEAD` 的 `examples/extensions/plan-mode` 零 diff | 仍是 example |

因此：`research-pi-1.0.md` 的三条最小 agent 结论（无 planner 节点、plan mode 是 example、无逐调用审批）在 HEAD 仍成立；`research-pi-agent-construction.md` 的「一个类 + 一个 options」「三层拼模式」「loop 无暂停态」也仍成立。**不需要为 D6 重读任何一条旧结论。**

## 6. 与本仓现役单 loop 的对照（只对现役形态）

| 维度 | pi（v1.3.0-10） | 本仓现役 |
|---|---|---|
| 控制流载体 | 一个 `while`（`agent-loop.ts:179-183`） | LangGraph 三节点 `agent → (finish\|tool)`（`graph.py:93-101`） |
| 一轮结束信号 | 没有工具调用（`agent-loop.ts:296`） | 没有工具调用 → `_last_action == "final_answer"`（`graph.py:49-51`），同 ADR 0008 决策 4 |
| 状态 | 类字段，事件归约（`agent.ts:565-603`） | `TypedDict`（`state.py:15-40`），由 checkpointer 持久化 |
| 步数上限 | **无** | `round_steps >= max_steps`（`graph.py:74`，默认 15，`nodes.py:89`） |
| 工具面 | `AgentState.tools` 数组，可随时赋值（`types.ts:400-401`） | `_REGISTRY` 类注册 + 宿主追加 MCP/Git/OpenAPI（`registry.py:32`、`task_manager.py:186-203`） |
| 工具声明 | transcript 里的 system message 差量（`agent-loop.ts:333-363`） | 每次请求随 `tool_schemas` 传（`nodes.py:107`、`task_manager.py:211`） |
| 压缩 | 宿主在 `prepareNextTurn`（`agent-session.ts:899-933`） | 节点内 `_build_messages`（`nodes.py:189-269`） |
| 模式 | 换 active tool 列表 + 注入消息（`plan-mode/index.ts:108,203`） | 状态里的 `mode` 字段 + 同一 `agent` 节点内部选 prompt/工具面（`state.py:20`、`nodes.py:108-114`） |

两边的**形状已经同构**：一条 loop、一个 agent 节点、以「无工具调用」收轮。差别集中在三处：pi 没有步数预算、pi 把工具声明放进 transcript、pi 的工具面是「宿主 Map + 名字列表」而不是可注册的工具类。

## 7. 对 D6 三个选项的证据性提示（不拍板）

### 选项 A（完整注册表，按单 loop 重写）

- pi 支持「包只贡献三样」这个切口：扩展能贡献的正是工具、事件、模型供应商（§3.1），**没有任何包级 manifest / 件清单**（§4）。若按 pi 的形状，A 的验收面应落在「工具 schema 快照 + 声明差量」，而不是「件清单 ±1 行」。
- 但 pi 的「装配」不是靠一张注册表完成的：工具集是 `createCodingTools` / `createReadOnlyTools` 这类**纯函数返回数组**（`tools/index.ts:195-211`），切换靠 `setActiveTools(names)`（`agent-session.ts:1523-1530`）。A 若只重写 `registry.py` 而不动 `task_manager.py:186-203` 那段 if 装配，得到的会是**两张并存的表**。

### 选项 B（退役能力包线）

- 有一手支持：核里根本没有注册表，只有 `tools` 数组（`types.ts:400-401`）；最小 agent 跑起来不需要「包」这个概念（§1.1 的 6 个文件）。
- 但注意：pi 的**宿主**仍必须有一张 `name → definition` 的 Map，否则做不到 `setActiveTools`、`getAllTools`、以及 1.3.0 那条「从 transcript 恢复 loadout」（`agent-session.ts:473-479`、`1809-1825`）。B 能退役的是「包/声明」这层叙事，**退役不掉「名字 → 实现」这张表**。

### 选项 C（缩成装配表）

- 与 pi 的形态最接近：pi 的装配面就是「一组工厂函数 + 一次 `setActiveTools` + 一张 definition Map」，没有 profile 对象（全仓 `profile` 命中 9 处，全部是 cache-warming / 图片 resize / AWS profile，与能力档无关）。
- pi 用「集合函数」表达 face（只读集 / 编码集，`tools/index.ts:164-211`），本仓已有同构物：`subagent.py` 的 `EXPLORE_TIER` + `tier_face_names`（`nodes.py:112-114` 引用）。C 的「装配表」可以直接建在这套词汇上，不需要新概念。
- 代价提示：pi 的极简是**把复杂度挪进宿主**——核 2101 行（loop+state+types），宿主 `agent-session.ts` 一个文件 4428 行。C 若只做「表」、不承接「声明面 / 恢复 / 只读档」，就会把 pi 在宿主里用数千行消化掉的这几件事留在原地。

## 8. 引用清单

均为 `E:\code\pi` @ `4dd2b149d`（= `v1.3.0-10-g4dd2b149d`）。

| # | 位置 | 用途 |
|---|---|---|
| 1 | `packages/agent/src/agent-loop.ts:38-61,71-100,163-321` | 循环入口与主循环 |
| 2 | `packages/agent/src/agent-loop.ts:179,183,245-256,286-292,296-318` | 两类 while、四种出口 |
| 3 | `packages/agent/src/agent-loop.ts:333-363` | `declareToolChanges`：工具声明差量 |
| 4 | `packages/agent/src/agent-loop.ts:508-523,690-692,708-777,859-910` | 工具调度、校验、前后 hook |
| 5 | `packages/agent/src/types.ts:33-37,222,244,326` | `StreamFn`、`convertToLlm`、`transformContext`、`beforeToolCall` |
| 6 | `packages/agent/src/types.ts:365-374,382-421,466-499,502-507,516-539` | 消息/状态/工具/上下文/事件类型 |
| 7 | `packages/agent/src/agent.ts:114-141,266-269,460-465,565-603` | `AgentOptions`、订阅、快照、事件归约 |
| 8 | `packages/agent/src/stream-fn.ts:11-19`、`packages/agent/package.json:25-28` | 默认 streamFn 插槽、仅两个依赖 |
| 9 | `packages/agent/CHANGELOG.md`（`[1.0.0]` Breaking / `[1.3.0]` 空段） | 掏空内核、1.3.0 无变化 |
| 10 | `packages/agent/README.md:83-107,508` | 事件序列、MCP 作为 AgentTool |
| 11 | `packages/coding-agent/README.md:19` | 「skips features like sub-agents and plan mode」 |
| 12 | `packages/coding-agent/docs/security.md:3,19` | 不逐调用审批、权限=OS 权限 |
| 13 | `packages/coding-agent/docs/how-pi-works.md:9-13,49` | 官方 loop 叙述、进程内权限 |
| 14 | `packages/coding-agent/docs/extensions.md:15,161` | 扩展工厂、工具不可注销 |
| 15 | `packages/coding-agent/src/core/extensions/types.ts:1001-1005,1592-1664,1670-1724,1747,1778,1858,1894` | 扩展 API 全貌、闭集条目类型 |
| 16 | `packages/coding-agent/src/core/extensions/loader.ts:289-301` | `registerTool` 实现 |
| 17 | `packages/coding-agent/src/core/agent-session.ts:473-479,526,653-681,777-786,899-933,1148-1168,1523-1530,1809-1825,1837-1882` | 注册表、审批接线、压缩、持久化、装配、恢复、宿主再入循环 |
| 18 | `packages/coding-agent/src/core/sdk.ts:46,348-365,382-409,411-446` | 注入 `streamSimple`、headers/重试、provider 钩子、构造 Agent |
| 19 | `packages/coding-agent/src/core/session-manager.ts:977` | JSONL append-only 树 |
| 20 | `packages/coding-agent/src/core/tools/index.ts:95-105,164-211` | 内置工具清单、只读/编码集合 |
| 21 | `packages/coding-agent/src/core/messages.ts:46-47` | 宿主唯一的 `role: "custom"` |
| 22 | `packages/coding-agent/examples/extensions/plan-mode/index.ts:108,117,164,203,329` | plan mode = 换工具集 + 注入 + follow-up |
| 23 | `packages/coding-agent/examples/extensions/question.ts:52,73` | 「问人」= 阻塞工具 |
| 24 | `packages/coding-agent/src/extensions/mcp/index.ts:444` | MCP 是扩展，工具动态注册 |
| 25 | `git diff --stat v1.0.4..v1.2.0 -- packages/agent`、`git diff --stat v1.2.0..HEAD -- packages/agent` | 版本区间结论（§5） |

本仓侧引用：`backend/core/agent/graph.py:49-51,74,93-101`、`backend/core/agent/state.py:15-40`、`backend/core/agent/nodes.py:89,107,112-114,189-269`、`backend/core/tools/registry.py:32,40-60`、`backend/services/task_manager.py:186-203,211`、`backend/services/event_bus.py:26`、`docs/capability-registry-rescope.md` §5（选项 A–C）。
