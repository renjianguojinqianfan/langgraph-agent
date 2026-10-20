# dsh 的「小心脏 + 可开关 + 可增删」一手调研

> **基线与方法**：本地源码 `E:\code\deepseek-harness`，HEAD `d743267388`，tag `dsh-v0.2.1-alpha.2`。只读源码与仓库内文档，未修改该仓库。
> **2026-10-19 注**：该克隆此后已更新到 `687f282e86`（`dsh-v0.2.1-rc.2`）；[决策日志](../capability-registry-rescope.md) §0.5 – §0.7 引用的 dsh 行号属新 HEAD，本文行号属旧 HEAD，引用前以各自基线复核。
> 下文 dsh 路径均相对 `E:\code\deepseek-harness`；标「本仓」的路径相对 `E:\code\demo\langgraph-agent`。
> 否定性结论都写了检索范围。量级数字用 `rg`/`Get-Content | Measure-Object -Line` 在本机 HEAD 上数出，只作量级参考。
> pi 侧不作为本次取证对象，引用本仓既有一手调研 `docs/research-pi-minimal-agent.md`（其基线是 pi `v1.3.0-10-g4dd2b149d`），并明确标注。

## 一句话结论

dsh 的「心脏小」不是代码少，而是**核只拥有语义与调度权，不拥有任何具体能力**：一个进程的组成由 profile（组合清单）决定，插件通过**服务 token + `inject` 依赖 + effect 回收**装配；关掉一个功能是**把那一行从组合里去掉（卸载并反向回收注册）**，不是运行时隐藏。真正的最小核来自 `inject` 闭包（7 个包），而不是目录 `packages/core`。

## 1. 内核边界

`docs/subsystems/core.md:5` 给了官方定义：core 是「每个组合都会启动的包」——事件溯源会话日志、system prompt 组装、工具注册表、Agent 类型、驱动它们的循环。`docs/architecture.md:55-65` 列了 7 个 core 包。

但**结构上的核由 `inject` 闭包决定，不由目录决定**：`dsh-agent-loop` 硬依赖 `agents / sessions / llm / tools / systemPrompt / sessionProjections`（`packages/core/agent-loop/src/index.ts:342`），其中 `session-projections` 不在 `packages/core` 下，却必须常驻。

| 包 / 服务 | 是否常驻 | 职责 | 证据 |
|---|---|---|---|
| `vendor/cordis` | 是（框架） | 插件、服务、事件、effect 生命周期 | `vendor/cordis/src/fiber.ts:195-196`（dispose=卸载） |
| `core/agent` (`ctx.agents`) | 是 | Agent 接口、活体注册表、`agent/*` 事件词汇 | `docs/architecture.md:62`；`docs/subsystems/core.md:16` |
| `core/agent-loop` (`ctx.agentLoop`) | 是 | 唯一的默认驱动：turn/step 循环、调 LLM、派发工具 | `packages/core/agent-loop/src/agent.ts:254`（turn 循环）、`:409`（step 循环）、`:453`（LLM stream）、`:549-552`（工具派发） |
| `core/session` (`ctx.sessions`) | 是 | append-only 事件日志；模型历史由日志派生 | `docs/architecture.md:59,124`；`packages/core/session/src/index.ts:522,800,984` |
| `core/system-prompt` (`ctx.systemPrompt`) | 是 | prompt 分节 + 工具 schema 组装 | `docs/architecture.md:60`；`packages/core/system-prompt/src/index.ts:474` |
| `core/tools` (`ctx.tools`) | 是 | 作用域工具注册表 + 受控执行管线（waterfall/guard） | `docs/architecture.md:61`；`packages/core/tools/src/index.ts:808-837,1063-1140` |
| `llm/llm` (`ctx.llm`) | 是 | 消息/流词汇 + adapter 注册缝 | `docs/architecture.md:65`；`packages/llm/llm/src/index.ts:389` |
| `session/session-projection` (`ctx.sessionProjections`) | 是（被硬注入） | 事件折叠成 host 可读投影 | `packages/core/agent-loop/src/index.ts:342,378-381`；`packages/bundle/sdk-minimal/cordis.patch.yml:47-48` |
| `core/scope` | 是（库，无 service） | 每 Agent 作用域注册原语 | `docs/subsystems/core.md:18-20` |
| `session/session-persistence` | 否 | 可选持久化缝；没挂就只在内存 | `packages/core/agent-loop/src/index.ts:396` 用 `ctx.get` 探测 |
| `plan/subagent/compaction/mcp/skill/todo/web/jobs/goal/...` | 否 | 产品能力，全部是插件 | `packages/bundle/sdk-minimal/README.md:12`；`packages/bundle/sdk-minimal/cordis.patch.yml` 无这些行 |
| `boot/*`、`host/*`、`apps/cli` | 不是核 | 启动器/宿主；launcher 只选 profile 并挂载 | `apps/cli/src/profile-boot.ts:1-10`；`packages/boot/app-boot/README.md:50` |
| `extensions/*` | 否 | 运行时写插件的元能力（inspect + 动态 runner） | `packages/extensions/README.md:12-17` |
| `runtime-diagnostics/invariants` | 否（已退役） | `ctx.invariants` 与各包 `./invariant` 子路径在 v0.2.0-rc.2 被删除；checkout 里只剩构建残留 | `docs/upgrade-guide/v0.2.0-rc.2/remove-runtime-invariants/guide.md:5-9` |

**依赖方向是单向的**：feature 包 peer 依赖 core，core 不依赖 feature。
`packages/core/agent-loop/package.json:28-38` 的 peer 只有 agent/llm/scope/session/persistence/projection/system-prompt/tools，没有任何 plan/subagent/mcp；反之 `packages/subagent/tool-subagent/package.json:34-44`、`packages/plan/plan-mode/package.json:38-48` 都把 core 服务列为 peer。`docs/module-graph.md` 的边方向与此一致。

**量化边界（HEAD 实测）**：`packages/core/*/src` 共 39 个 `.ts`、13,555 行；其中 agent-loop 2,326 行、tools 5,350 行、session 3,113 行。`dsh-base` 的 patch 有 **94** 个 `- id:` 行（含 9 个 `disabled`），`sdk-minimal` 只有 **30** 个。所以「dsh 心脏小」在代码量上不成立；成立的是**耦合小、可替换**。

## 2. 插件契约与生命周期

一个插件有两种写法：函数插件 `{ name, inject, apply(ctx) }`，或 `Service` 子类（`super(ctx, 'key')` 注册自己）。`Config`（schemastery/zod）声明配置 schema。最小实例：`packages/session/tool-working-directory/src/index.ts:11-19`。

**能贡献什么**（同一插件可同时贡献多项，`plan-mode` 就是 service + 事件 + prompt + command + tool + projection 六合一，`packages/plan/plan-mode/README.md:103`）：

| 贡献 | 接口 | 证据 |
|---|---|---|
| 服务 | `Service` 子类 / `ctx.provide`，占一个 `ctx.<key>` | `docs/cordis-tutorial/03-services.md:20-24,39-40` |
| 事件监听（即 hook） | `ctx.on('agent/*' / 'tools/*', ...)`；waterfall 可拦截 | `docs/cookbook/extension-cookbook.md:13` |
| 工具 | `ctx.tools.register(definition)` | `packages/core/tools/src/index.ts:1069` |
| 人类命令 | `ctx.commands.register(definition)` | `packages/interaction/commands/src/index.ts:285` |
| Prompt 分节 | `ctx.systemPrompt.section(...)` | `packages/core/system-prompt/src/index.ts:474` |
| LLM provider | `ctx.llm.registerAdapter(providers, adapter)` | `packages/llm/llm/src/index.ts:389` |
| 能力 backend | `ctx.fs` / `ctx.shell` / `ctx.sandbox` / `ctx.subagents` / `ctx.compaction` | `docs/architecture.md:144-153` |
| 设置表单 | 插件自己的 `Config`，由 `ctx.settings` 投影 | `docs/subsystems/settings.md:5,9`；`packages/settings/settings/src/index.ts:223-224` |
| 客户端 UI | `package.json` 的 `dsh.client.inject` + `./client` 导出 | `packages/client/ui-workspace/package.json:28-44` |
| 动态外部工具 | MCP server 发现后注册；重连换一代、重连预算耗尽后注销 | `packages/mcp/mcp-client/README.md:81,129` |

**依赖与激活顺序**：不是声明顺序，而是 `inject` 的服务可用性。`docs/cordis-tutorial/03-services.md:59` 明确「行顺序无关，依赖决定何时启动」；`packages/bundle/base/cordis.patch.yml:13` 也写「Row order carries no load semantics」。可选依赖不进 `inject`，在用到的地方 `ctx.get` 探测（`03-services.md:82-88`）。还有一种中间形态：`ctx.inject(['sessionPersistence'], childCtx => ...)` 生成一个「依赖出现才激活」的子 fiber（`packages/core/agent-loop/src/index.ts:410-420`）。

**停用 / 卸载**：
- 停用 = Loader entry `disabled` → `fiber.dispose()`，不是挂起。`vendor/loader/src/config/entry.ts:136-138`。
- 所有注册都是 effect，卸载时**反向**执行 disposer。`vendor/cordis/src/fiber.ts:71-72,195-196`。
- 插件管理器的 toggle 只改 profile patch 里的 `disabled`；有 HMR 就当场 reconcile，没有就 `restart-required`。`packages/boot/plugin-manager/src/index.ts:452-467,819-821,833`。
- 卸载包 = 先取消选择并 unload，再 pnpm remove；没有 HMR 时要求先停 profile。`packages/boot/plugin-manager/src/index.ts:639-672`。
- **没有框架级的 suspend/resume**：`rg "suspend|resume" vendor/cordis/src` = 0 命中；`rg "ctx\.hooks" packages` = 0 命中。`unregister` 的命中都是局部 disposer 变量名/文档，不是框架 API。停用语义只有「不加载」和「卸载回收」两种。

## 3. 功能开关的真实语义

| 机制 | 是什么 | 「关掉」时发生什么 | 证据 |
|---|---|---|---|
| **profile** | 进程级命名组合，存在 `$DSH_HOME/profiles/<name>`：有序 bundles + 自己的 patch + 外部插件 | 换 profile = 换一棵进程内的插件树；模板 `web/headless/sdk/sdk-minimal/acp` | `docs/architecture.md:17`；`packages/boot/app-boot/src/profile.ts:179-195` |
| **bundle** | 一种发行格式：一个 `cordis.patch.yml`，插入若干行 | 取消选择 = 这些行整层消失；不是「隐藏」 | `docs/architecture.md:19-21`；`packages/boot/plugin-manager/README.md:40` |
| **plugin（行）** | 最小加载单元，`- id/name/config/disabled` | `disabled` → 不 import、不 mount，已注册项反向回收 | `vendor/loader/src/config/entry.ts:136-138` |
| **preset** | 每 Agent 的子插件行（`config.plugins`），可多个并存、按 revision 保留 | 选中哪个 preset，就只挂哪套工具/prompt/skill；选择写进 session 日志 | `packages/preset/agent-preset-registry/README.md:12,46,56,60` |
| **settings** | 活跃行的 `Config` 字段值（可 volatile） | **只改值，不装卸代码**；没有「enabled」这个概念（`rg enabled packages/settings/settings/src` = 0 命中） | `docs/subsystems/settings.md:5,9` |

所以「关掉一个功能」在 dsh 里其实有四种不同语义，设计时必须先选：

1. **不加载**：`disabled: true` / 去掉 bundle / 删包。最彻底，注册全部消失（`entry.ts:136-138`）。
2. **对某个作用域隐藏**：工具仍注册，但该 Agent 的可见集被 allow/deny 过滤。`packages/core/tools/src/index.ts:1103-1129`；子 Agent 的 `toolFilter` 走同一机制（`packages/subagent/subagent/src/child-agent.ts:216`）。
3. **执行时拒绝**：工具可见可调用，但 sandbox/guard/waterfall 拒绝执行。`docs/subsystems/sandbox.md:11,23`；`packages/core/tools/src/index.ts:1132-1140`。
4. **只改 prompt**：plan mode 就是这一类——**工具目录完全不变**，只换一段政策文本。`packages/plan/plan-mode/README.md:12,93,175`；base 内置的 plan 政策文本直说这是为了 request-cache 稳定（`packages/bundle/base/cordis.patch.yml:335`）。

还有一个「条件挂载」：`disabled` 可以是 `!!js` 表达式，在 mount 时对 ctx 求值。base 用它做 `!!js "!ctx.get('profileContext')"`（`packages/bundle/base/cordis.patch.yml:22,30`），平台选择也这么做（`:246,252`）。这比在代码里写 `if feature_enabled` 更可组合。

**没有第二层 profile**：profile 是进程级；per-Agent 的「小 profile」就是 preset。preset 要替换某个服务实现时，必须放在同一个 `cordis:group` isolate realm 里，普通 `agent.ctx` 查到的仍是 Host realm（`packages/preset/agent-preset-registry/README.md:58`）——这是重资产，不是默认能力。

## 4. 服务装配与工具注册

### inject vs ctx.get

| 用法 | 语义 | 代价 | 证据 |
|---|---|---|---|
| `static inject = [...]` / `export const inject` | 硬依赖；服务缺失则 PENDING，服务消失则连消费者一起卸载 | 依赖面被放大，reload 会连带重启 | `docs/cordis-tutorial/03-services.md:59,76` |
| `ctx.inject([...], cb)` | 生成子 fiber，依赖满足才跑；返回 fiber.dispose | 多一层生命周期 | `packages/core/agent-loop/src/index.ts:410-420` |
| `ctx.get('key')` | 用点探测的可选能力；没有就降级 | 调用方自己处理 undefined | `03-services.md:82-88` |

dsh 对第三种给了非常直白的取舍：`dsh-tools` 故意**不**把 `ptcRuntime` 放进 `inject`，注释的意思是否则会拿 `ctx.tools` 和它背后的所有工具插件当人质，去换一个即使 `mode: 'native'` 根本用不到的运行时（`packages/core/tools/src/index.ts:1037-1055`）。这条是本调研里最值得抄的一条工程判断。

**服务 token 的实现**：运行时就是一个扁平名字空间里的 `ctx.<key>`（`super(ctx, 'greeter')`），编译期靠 TypeScript declaration merging 让 `ctx.greeter` 有类型；不写 merge 也能跑，只是没类型（`03-services.md:39-40`）。**核不认识插件**，因为核只声明 key + 接口，消费者按 key 取；`agent-loop` 的 package.json 里没有任何 feature 包（见 §1）就是证据。

### 工具注册的形态

| 形态 | 适合 | 证据 |
|---|---|---|
| `defineTool` + `ctx.tools.register` | 一方工具，带类型化参数与输出契约 | `packages/core/tools/src/schema.ts:554`；`.../src/index.ts:1069` |
| 直接注册 raw JSON-Schema `ToolDefinition` | 外部/生成工具（MCP 就这样进来） | `docs/cookbook/extension-cookbook.md:9` |
| 作用域注册（`agent.ctx` / preset scope） | 只对某个 Agent 可见，遮蔽全局同名工具 | `packages/core/tools/src/index.ts:1064-1065` |
| 动态代际同步 | MCP：一次发现注册一批，重连换一代，重连预算耗尽后整代注销 | `packages/mcp/mcp-client/README.md:81,129` |
| 呈现面收窄 `presentAs('native'\|'ptc')` | 不删工具，只把多个 schema 收成一个 `run_code` | `packages/core/tools/src/index.ts:983-1034` |
| 可见性过滤 `restrict({allow,deny})` | 每 Agent 的工具面；只能在 scoped ctx 用 | `packages/core/tools/src/index.ts:1103-1129` |
| guard / `tools/pre-execute` | 策略拒绝，不是注册形态 | `packages/core/tools/src/index.ts:1132-1140`；`extension-cookbook.md:13` |

**按需收窄的三个现成例子**：
- subagent：`toolFilter` 在子 Agent 创建窗口里 `restrict`，`maxDepth: 0` 禁止再委托；到上限时工具**仍然可见**，调用才报错（`packages/subagent/tool-subagent/README.md:52,63`）。
- sandbox：不摘工具，只包 argv；`danger-full-access` 直接绕过（`docs/subsystems/sandbox.md:11,23`）。
- plan mode：不摘工具，只换 prompt（见 §3）。

## 5. 与 pi 的边界对照

pi 侧取自本仓 `docs/research-pi-minimal-agent.md`（一手 clone 调研）：核 = 一个 `while` 循环 + 一份可写状态 + 外部注入的 `StreamFn` + 可选 hook，不含工具实现/持久化/压缩/模式/MCP，审批只有一个 `beforeToolCall` 钩子（`:9-10,18-25,52`）。

| 维度 | dsh | pi | dsh 为什么放进核 |
|---|---|---|---|
| 循环 | 核（agent-loop 插件，但硬注入闭包内） | 核 | 都认为循环是不可替换的心脏；dsh 仍留了 `AgentFactory` 接口（`docs/subsystems/core.md:51`） |
| 状态 | **事件溯源日志**是核，持久化是可选缝 | 可写内存状态是核，持久化在宿主 | 「model-visible means logged」：所有能力从日志投影（`docs/architecture.md:124`） |
| LLM | 核里是 registry/seam，adapter 是插件 | 核里只声明 `StreamFn` 形状 | 换 provider 是配置行为（`docs/architecture.md:9`） |
| 工具表/调度 | 核：注册表 + 受控管线 + presentation | 核：name→definition 表 + 前置 hook | 动态 MCP/策略/审批需要唯一一条受控派发（`tools/src/index.ts:804-837`） |
| 工具实现 | 插件 | 宿主 | 同 |
| 压缩 | 插件（capability seam） | 宿主 | `docs/subsystems/compaction.md:5` |
| 模式 | 插件；plan 只改 prompt | 宿主；改 active tool 列表 | 见 §3 的 cache 取舍 |
| 子 Agent | 插件 + provider seam | 宿主/扩展 | `packages/subagent/subagent/README.md:34-45` |
| 审批/沙箱 | 插件 seam，执行前包 argv | 宿主 hook，核无内置审批 | `docs/subsystems/sandbox.md:5` |
| 多 Agent 注册表 | 核（`ctx.agents`） | 无同等核级注册表（本仓调研未列） | 一个进程多会话/子 Agent 是产品前提 |

量级对照：pi 核约 2,101 行（`docs/research-pi-minimal-agent.md:145`）；dsh 仅 `packages/core/*/src` 就 13,555 行。**结论：dsh 不是「更小的 pi」，它是「用更大的契约面换可替换性」。** 两者相同的是「循环在核」；不同的是 dsh 把**会话日志、工具管线、事件词汇、多 Agent 注册表**也当成核，因为这些是它多 surface（web/desktop/CLI/SDK）组合的前提。

## 6. 对本仓的最小可行形态

### 现状对照

本仓已经有一个接近 pi 的核：一个 loop / 一个 `agent` 节点（`AGENTS.md:21`）、`ToolRegistry`（`backend/core/tools/registry.py:32,40,133-139`）、插件自动发现（`registry.py:9-13`；`backend/services/task_manager.py:184-186`）、EventBus（`backend/services/event_bus.py:18-34`）、MCP/Git/OpenAPI 显式追加（`task_manager.py:196-203`）。缺的不是「再拆一层」，而是**插件契约与组合清单**：插件发现机制目前只认工具（`backend/core/tools/registry.py:9-13`；`backend/plugins/example_tool.py:30-31`），开关是全局 settings 布尔（`backend/config.py:86-87`）加代码里过滤（`task_manager.py:204-210`）。

### 抄 / 不抄

| 抄 | 理由 | 不抄 | 理由 |
|---|---|---|---|
| 服务 token + `get` 探测 | 让核只认接口，不认实现；成本一个字典 | bundle 分层 patch YAML | 那是多发行版分发机制，我们只有一套产品 |
| 硬依赖 `requires` + 软依赖 `get` | 激活顺序自动化，缺依赖可诊断 | pnpm/registry/peer 版本/构建脚本审批 | 完整的包管理器是另一个产品 |
| 注册即 effect + 反序 disposer | 卸载可证明地干净（Python 用 `ExitStack`） | HMR / 事务性 reconcile | 我们重启成本极低 |
| 行级 `enabled` + 启动时组合 | 「关掉」= 不 import，不留半挂状态 | 每 Agent 独立 Loader 树 / isolate realm / preset revision | 一个进程一个组合就够 |
| 事件作为策略缝 | 新策略不改核、不改图 | 客户端双半包 / UI 插件树 | 我们的前端是单体 |
| 每 Agent `restrict` 可见集 | 一个进程内不同任务不同工具面 | 类型声明合并 | Python 用 Protocol/注册表即可 |
| 一个真正的最小组合做回归 | 防止核悄悄长胖 | 五声明文档 / 件清单 ±1 行 / 冻结区 | 见下 |
| — | — | 运行时 invariant registry | dsh 自己在 v0.2.0-rc.2 删了（`docs/upgrade-guide/v0.2.0-rc.2/remove-runtime-invariants/guide.md:5-9`） |

**一个安全警示**：dsh 的插件是**进程内全权限代码**，不是沙箱；装插件等于装可执行代码（`packages/boot/plugin-manager/README.md:31`；`docs/subsystems/claude-code-mods.md:16`）。本仓若做「插件增删」，必须把「安装」定义成受信任操作；不要用插件机制承载不可信第三方代码。

### 最小可行形态（建议，不是拍板）

1. **核保持 6 件**（都已有）：loop/图、状态/checkpoint、LLM client seam、ToolRegistry + dispatch、EventBus、config loader。**不要**再拆节点或加模式分支。
2. **加一个薄 `PluginContext`**：`get(name)` / `require(names)` / `on(event)` / `tool(def)` / `effect(disposer)`。约 100 行，不需要 DI 框架。
3. **插件清单**：每包一个 `plugin.toml`（`name/version/requires/provides/tools/subscribes` + 可选 config schema）。`requires` 做拓扑激活；缺失硬依赖 = 跳过并给诊断，不崩进程；软依赖用 `get`。
4. **组合清单**：`profiles/minimal.toml`、`profiles/full.toml` 列模块路径；不在清单里 = 不 import。**这就是「最小核 profile」，是数据不是代码分支**——dsh 的 `sdk-minimal` 正是 30 个插件行的 YAML + 一个 bundle 声明（`packages/bundle/sdk-minimal/cordis.patch.yml:1-3`；`.../package.json:31-34`）。
5. **工具面**：每次 run 计算 `visible = registered ∩ profile.allow ∩ task.allow`；plan mode 用「prompt 约束」还是「visible 收窄」要单独拍板。dsh 选 prompt 是为了 cache 稳定（`base/cordis.patch.yml:335`），本仓现在选的是 dispatch 时拒绝档外工具（`AGENTS.md:21`，`backend/core/agent/nodes.py:108-114`）——后者更强，别因为「dsh 这么做」就退掉。
6. **卸载**：删清单行 + 重启；不做热卸载。插件自身 `effect` 反序回收，保证单测里 register→dispose→工具消失。
7. **验证面**：两个 profile 各跑一次启动；工具 schema 快照 diff；一个「注册后卸载，工具面归零」的单测；一个「缺硬依赖只跳过该插件」的单测。**不要**再引入件清单行数断言。

### 「五声明 / 件清单 / 最小核 profile」还要不要？

- **五声明**：降级成 `plugin.toml` 的机器可读字段（`name/version` 之外的 requires / provides / tools / config keys），文档自动生成。把「文档表格当契约」换成「manifest 当契约」，否则注册表和文档会漂移。旧五声明在 `docs/specs/capability-package-architecture.md` §3（加退役抬头后在 `:34-46`），其中件清单已被 `docs/capability-registry-rescope.md` §1 第 1 条判为失效。
- **件清单**：**退役**。dsh 的等价物是「启动是否成功 + 缺哪个服务」的诊断（`packages/boot/app-boot/README.md:43,107`），不是一份手写清单；`±1 行` 这种断言把装配问题变成了格式问题。
- **最小核 profile**：**保留，但只作为一份组合清单 + 一条启动回归**。dsh 的 `sdk-minimal` 证明这可行：没有 `mode="minimal"` 代码分支，只有一个独立的 30 个插件行 patch 和「显式拥有每一行、不继承 base」的声明（`packages/bundle/sdk-minimal/cordis.patch.yml:1-3`；`README.md:12`）。

**够不够？** 对本仓规模，「服务 token + 插件清单 + 工具集」**够**，但要补两块 dsh 用框架兜住的东西：① `requires/provides` 声明的激活与诊断；② 注册的 disposer/反序回收。没有这两块，清单只是 import 列表，卸载会留幽灵注册。**不需要**的是 profile 包管理器、HMR、bundle 层叠、per-Agent 隔离树——那些是 dsh 为「多 surface + 第三方发行」付的价。

## 引用清单

**dsh 架构与边界**
1. `docs/architecture.md:9,11,17,19,21,25` — 一切都是插件；无特权核；profile/bundle/patch 层叠。
2. `docs/architecture.md:55-65` — core 包表；`:124` model-visible means logged；`:138-160` 新行为挂载点。
3. `docs/subsystems/core.md:5,9,18-20,51` — core 定义、六包一条 loop、scope 是库、AgentFactory 可替换。
4. `packages/core/agent-loop/src/index.ts:342,378-381,396,410-420` — 硬注入闭包、投影注册、持久化探测、条件子 fiber。
5. `packages/core/agent-loop/src/agent.ts:254,409,453,549-552` — turn/step/stream/tool 派发。
6. `packages/core/session/src/index.ts:522,800,984` — Session、append、deriveMessages。

**插件与生命周期**
7. `docs/cordis-tutorial/03-services.md:20-24,39-40,59,76,82-88` — token、effect、inject、可选 get。
8. `vendor/cordis/src/fiber.ts:71-72,195-196`；`vendor/loader/src/config/entry.ts:136-138` — 反序回收；disabled 即 dispose。
9. `packages/core/tools/src/index.ts:804-837,983-1034,1037-1055,1063-1140` — 工具管线、presentation、懒探测取舍、register/restrict/guard。
10. `packages/interaction/commands/src/index.ts:285`；`packages/core/system-prompt/src/index.ts:474`；`packages/llm/llm/src/index.ts:389` — 其余贡献面。
11. `docs/cookbook/extension-cookbook.md:9,13` — raw schema 工具；native hook 就是普通插件。
12. `packages/boot/plugin-manager/src/index.ts:452-467,639-672,819-821,833`；`README.md:14,31,40` — toggle、remove、reload、restart-required、进程内全权限。

**开关语义**
13. `packages/boot/app-boot/README.md:43,50,54,107` — required 列表、profile 位置、composed 边界、缺服务等待。
14. `packages/boot/app-boot/src/profile.ts:179-195,223-235` — 5 个模板；官方 optional bundles。
15. `packages/bundle/base/cordis.patch.yml:1-13,22,30,246-252,327-345` — patch 语义、条件挂载、plan-mode 文本、cache 理由。
16. `packages/bundle/sdk-minimal/cordis.patch.yml:1-3`；`README.md:12` — 独立最小树与它明确排除的能力。
17. `docs/subsystems/settings.md:5,9` — settings 只投影活跃行的 Config。
18. `packages/preset/agent-preset-registry/README.md:12,46,56,58,60` — preset 每 Agent 组合、revision、isolate realm。
19. `packages/plan/plan-mode/README.md:12,93,175` — 计划模式不摘工具。
20. `packages/subagent/tool-subagent/README.md:52,63`；`packages/subagent/subagent/src/child-agent.ts:216` — toolFilter/maxDepth。
21. `docs/subsystems/sandbox.md:11,23` — 沙箱是执行策略不是工具面。
22. `docs/upgrade-guide/v0.2.0-rc.2/remove-runtime-invariants/guide.md:5-9` — 运行时 invariant 注册表已整体删除。

**量级与对照**
23. 本机 HEAD 计数：`packages/core/*/src` 39 files / 13,555 行；base patch 94 `- id:`；sdk-minimal 30 `- id:`。
24. 本仓 `docs/research-pi-minimal-agent.md:9-10,18-25,145` — pi 核形状与 2,101 行量级（一手 clone 调研）。

**本仓现状**
25. `AGENTS.md:21`；`backend/core/agent/nodes.py:108-114` — 一 loop 一节点；plan mode 的 explore 档。
26. `backend/core/tools/registry.py:9-13,32,40,133-139`；`backend/services/task_manager.py:184-186,196-210`；`backend/services/event_bus.py:18-34`；`backend/config.py:86-87`；`backend/plugins/example_tool.py:30-31`。
