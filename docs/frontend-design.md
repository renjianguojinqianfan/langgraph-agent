# 前端设计基线（Frontend Design）

> **状态**：设计权威，**已交付**（2026-10-23 创建；2026-10-27 按 [`docs/specs/frontend-redesign-plan.md`](specs/frontend-redesign-plan.md) 的 F1–F9 + B1 落地；#189 已由 F1 修复）。现役代码按本基线交付，视觉与交互以本文为准。
> **权威顺序**：功能范围 → #54（含 2026-10-19 / 2026-10-23 校准段）；视觉与交互规则 → 本文；接口契约 → `architecture.md` §3.3 / §3.4（SSE 词汇表有 `npm run check:sse` 守卫）。
> 本文只写规则与判据：实现步骤写进 PR，需求原文留在 #54，不在此复制。

## 0. 方向与三个承诺

**安静的监控台**：一本能说话的飞行记录仪。左列任务、中列对话、右列工作区；子智能体是一条压在对话上方的飞行条。

1. **记录是唯一权威**：状态、计划版本、文件读数一律来自任务快照（REST）；SSE 只提供节奏，事件回放不得改写快照（#189）。
2. **机器事实用 mono，人话用 Inter**；一个信号蓝只标「此刻 / 可操作」。
3. **三种对象三种形状**：记录 = 行、子智能体 = 条、回答 = 平铺正文（#265 起按 dsh 形态不再套纸面；过程收进上方的折叠行）。

## 1. Token

| 语义 | 变量 | 值 | 白名单 |
|---|---|---|---|
| 页底 | `--surface-page` | `#0A0A0B` | 全局底 |
| 列面 | `--surface-column` | `#131316` | 左右栏、header、页签条 |
| 纸面 | `--surface-sheet` | `#17171B` | 设置对话框内的槽底、跳转链接；#265 起回答平铺，内容区不再有纸面 |
| 槽底 | `--surface-well` | `#121215` | 代码块、工具入参出参、输入框 |
| 悬停底 | `--surface-hover` | `#202027` | 行 / 按钮 hover、用户气泡 |
| 主文字 | `--text-primary` | `#E8E8EA` | 标题、回答、用户记录 |
| 次文字 | `--text-secondary` | `#A6ABB2` | 正文、步骤描述 |
| 三文字 | `--text-tertiary` | `#8B9097` | 机器事实、时间、计数 |
| 发丝线 | `--rule` / `--rule-strong` | `rgba(255,255,255,.07)` / `.12` | 行分隔 / 控件环 |
| 信号 | `--signal-now` | `#4C8DFF` | 仅五处：运行点、发送键、焦点环、链接与行内代码、当前文件 |
| 蓝底字 | `--text-on-signal` | `#0A0A0B` | 信号蓝底上的字形（发送键），6.18:1 |
| 状态 | `--state-ok` / `--state-warn` / `--state-danger` | `#3FB97F` / `#D9A441` / `#E5534B` | 状态点与状态词；不做大块底色 |

- 选中态（页签、模式、任务行、文件行）用墨色 + 2px 内嵌脊线；**信号蓝不参与选中**。
- 光从上方来，只上一级：页 → 栏 → 纸。发丝线只做分隔；无渐变、无辉光、无网格、无装饰边框。
- 圆角 token：`--radius-sheet 11`、`--radius-dialog 14`、`--radius-well 8`、`--radius-composer 12`、`--radius-control 6`（2026-10-27 补全，D3）、胶囊 999；**行与页签 0**。
- 状态色分工：`--state-ok` 只用于工具「成功」与产物/成功标记；任务状态用 `--signal-now`（运行）/ `--state-warn`（停下）/ `--state-danger`（失败）/ `--text-tertiary`（启动）。
- **机械守卫**：机器可读镜像 `DESIGN.md` 的 frontmatter 与实现 `frontend/src/index.css` 的 `@theme` 由 `scripts/check_design_tokens.py` 按守卫表逐项比对（颜色 / 圆角 / 排版三组；字体 fallback 全串与 `fontFeature` 刻意不比，理由见脚本头注）；pre-commit 钩子 `design-token-mirror` 在提交时拦。**守卫只保「镜像 ↔ 实现」，本表本身仍由人维护**——改 §1/§2 时要人工把镜像与实现一起对齐，再跑该脚本。

> **本文值为准**：旧 `#63676D`（3.26:1）不足 AA；`#9BA0A6` 与 `--rule` .06/.11 一并提亮，是为统一色阶（不改变其对比度结论）。本文用 `#A6ABB2` / `#8B9097`、`.07` / `.12`。

## 2. 字体与排版

| 角色 | 字体 | 规格 | 用在哪 |
|---|---|---|---|
| 标题 | Inter | 15.5 / 1.4 / 600，`-0.01em` | 任务标题、回答标题、对话框标题 |
| 正文 | Inter | 13 / 1.85 / 400 | 对话、回答、计划、输入框 |
| 次级 | Inter | 12 / 1.6 / 400–500 | 标签、按钮、说明 |
| 机器事实 | JetBrains Mono | 11 / 1.5 / 400 + `tabular-nums` | 路径、工具名、计数、时长、时间、ID |
| 代码正文 | JetBrains Mono | 12 / 1.7 | 代码块、文件预览 |
| 空态一句 | Inter | 21 / 1.35 / 500 | 未选任务时的邀请句（全局唯一 >15.5） |

- **mono 是语义不是纹理**：只写机器拥有的事实；中文标签、状态词、标题永远 Inter。
- 数字一律 `tabular-nums`；用于比较的数字不做截断。
- 标题 `text-wrap: balance`；正文段落 `text-wrap: pretty`；除标题 `-0.01em` 外不加字距、不拉宽标签、不全大写。
- 标题层级：页面唯一 `h1`（任务标题）；回答标题 `h2`；计划文档从 `h2` 起（`h3` 小节）；对话框自成一级。
- **依赖（2026-10-26 批，F2 已安装并使用）**：`@fontsource-variable/inter`、`@fontsource/jetbrains-mono` 已在 F2 装入 `main.tsx`；`react-markdown` + `remark-gfm` 与 `vitest ^5.0.5` 同批批准，UI 层零新增依赖。Markdown 已接（F5，`lib/markdown.tsx`）：不开原始 HTML（`skipHtml`、无 `rehype-raw`），标题从 h2 起（h1 映射为 h2）。

## 3. 布局与三栏职责

三栏固定：**264 / 流式（内容测度 760）/ 420**。中栏自上而下：header 52 → 子智能体条（有才出现）→ 对话（唯一滚动区）→ composer（常驻）。

| 栏 | 放什么 | 不放什么 |
|---|---|---|
| 左栏 264 | 新任务、搜索、目录上下文（本目录 / 全部）、任务历史（分组、状态点 + 词、相对时间）、工作区切换与设置入口 | 任务详情、文件内容 |
| 中栏 | 任务 header（标题 / 模式词 / 目录 / 状态 / 停止 / 右栏开关）、子智能体条、对话记录、计划入口行、回答正文、composer | 常驻文件树、第二个输入框 |
| 右栏 420 | 页签：文件 / 计划 / 事件 + 每个打开文件的预览页签（#266，dsh 形态；事件页底部「打开完整 Trace」→ 中栏全宽回放面，复用现役 `TraceTab`）；计划编辑就地展开 | 第四栏、子智能体页签（子智能体只在顶部）、卡片套件 |

- 子智能体条：**只在顶部**、可折叠、只在任务含子智能体时出现；点卡片在条下就地展开详情。只有执行模式可能有子智能体（计划模式的工具面没有 `spawn_subagent`）。
- 对齐：全部左对齐；唯一右对齐的是用户气泡，以及单元格内需要比较的数字。
- 断点：<1400 路径芯片收窄；<1180 右栏变抽屉（220ms `cubic-bezier(.32,.72,0,1)`）；<860 左栏变抽屉。断点单一来源 `frontend/src/lib/breakpoints.ts`：`(width < 860px)` / `(width < 1180px)`，与 CSS `max-[860px]` / `max-[1180px]` 同界——860 / 1180 恰好仍是宽屏；开关渲染与遮罩派生都读这一来源（#257；取代 #238/#240 的 859.98 / 1179.98 对齐写法）。全屏布局补 `env(safe-area-inset-*)`。
- 滚动：对话区与抽屉/对话框 `overscroll-behavior: contain`。

## 4. 组件与状态

| 组件 | 规则 |
|---|---|
| `TaskHeader` | 标题（15.5、单行截断、`title`）；第二行「模式词 + 工作目录」（mono、截断、`title`）；右侧状态、停止、右栏开关。模型只在 composer。 |
| `TaskRail` | 左栏：搜索、目录上下文（本目录 / 全部）、任务行（状态点 + 词 + 相对时间）、设置入口；行截断 + `title`。 |
| `Status` | 单一来源：启动中 / 运行中 / 已停下 / 失败；色点 + 文字双通道；`role=status aria-live=polite`。 |
| `SubagentStrip` / `Plate` | 平板条（无阴影、发丝分栏）；每张 = 名字 + 状态词（Inter）+ 机器事实（短 id + 产物数，mono；无 token / 耗时）；名字截断 + `title`；点开就地展开详情（摘要 / 产物 / 边界说明）。 |
| `TurnProcess` | 每轮一条过程折叠行（#265，dsh 形态）：运行点 + 已完成 / 执行中 / 处理失败 + 步数 + 工具次数；运行中展开、停下后自动折叠；展开只含本轮 `StepRecord`，压缩标记挂在所属步（归属在 `Conversation` 一次算清）；找不到所属步的压缩标记单独收在对话末尾的「上下文压缩记录」。 |
| `StepRow` | 行形状（无卡、无圆角）；左 gutter 放状态点；主体 = 一句话 + 右列机器事实；点开原地展开工具行。展开区在工具行之前渲染可折叠「思考」块（#267 / D-40）：默认折叠、空/缺省不渲染（非思考模型无空块），正文按长内容断行并限高内部滚动；行头在存在思考时标一个「思考」机器事实词。 |
| `ToolRow` | 槽底；工具名与参数 mono、可截断 + `title`；结果词只用**终态**三值：成功 / 失败 / 未执行（`success / failed / skipped`；`pending` 是进行中，不改结果词。原因写进 `error`；不新增 `blocked` 状态）。 |
| `PlanAnchor` | 对话里一行入口：「计划 v3 · 状态」（版本 = `Task.plan_version`；状态词由 `Task.status` 派生，不是计划自己的字段）；点开切右栏计划页；不复制全文。 |
| `AnswerSheet` | 平铺回答正文（#265 按 dsh 形态去掉纸面、阴影与 provenance 头部）；Markdown（标题 / 列表 / 代码 / 行内代码）；空回答显示「（空回答）」。每轮的过程与计数在上一行的 `TurnProcess`，回答本身不再重复。 |
| `Composer` | 模式段 + 模型控件（原生 `<select>` + `<optgroup>` 按 Provider 分组，chip 形态、不新做浮层；`目录外模型…` 分支给 Provider + 裸模型 ID，切换走 `{provider, model}` 自动建 manual 条目）+ 输入 + 发送（蓝底 + `--text-on-signal` 字形，6.18:1；不用白字 3.20:1）；Enter 发送、Shift+Enter 换行，提示只出现一处；运行中输入可编辑（`/stop` 与下一轮草稿），发送禁用并说明原因；启动中 / 加载 / 加载失败禁用。 |
| `FilePane` | 可折叠文件树：根展开、其余折叠；目录优先、名称自然排序；当前预览文件的祖先自动展开；按名称/路径过滤只留命中分支并展开祖先，清空恢复原展开；点文件名在第三栏开预览页签（同一路径复用页签），行保留大小与下载；>2000 条明示截断；`role=tree`/`treeitem` + 上下移动、左右折叠/展开、Enter 打开、Home/End，`aria-expanded` / `aria-level` / `aria-selected`；2s 轮询保留展开、滚动与当前文件，切任务重置。 |
| `FilePreviewTab` | 每个打开文件一个页签（#266/#344）：只读代码预览——头部面包屑（目录段 + 文件名；深路径从左侧折叠为 `…`，420px 不溢出）、大小与下载；正文是独立行号 gutter（`aria-hidden` + `select-none`，行号不进复制文本）加等宽代码，按扩展名用 Shiki 按需高亮（`ts/tsx/js/jsx/json/md/py/sh/css/html/yaml/toml`，未知扩展名回退纯文本），`tab-size: 2`，长行只在正文内横向滚动（gutter sticky）；最多渲染 2000 行，后端 200KB 截断与 2000 行上限各有提示，空文件给「空文件」空态；下载入口在所有状态都保留。同一路径复用页签；关闭页签回到相邻页签（优先左侧），无页签时回文件列表；从文件列表打开后焦点落在新页签上。失败按 HTTP 状态分行：415 二进制 / 400 路径越界 / 404 文件不存在 / 409 工作区不存在；文件列表面板保持挂载，切回时保留滚动位置；预览首次被激活且右栏可见时才取数（隐藏页签不发请求）。 |
| `FileNavigator` | #346 浮动树（布局 C，不做硬分栏）：预览 header 的树 toggle 开关；420 行宽下浮在预览上（默认 260、最小 180、最大 `min(300, row-120)`），预览保持整宽；≥560 行宽为贴边 raised panel，预览 body ≥320（树最大 `min(300, row-320)`）；行 <400 或视口 <768 在 header 下铺满，打开文件即收起；宽度可拖拽与 ←/→、Home/End 调整，按 task 记忆；树数据/展开/筛选/滚动沿用 #341，预览沿用 #344。打开焦点进入树，Esc 关闭并回 toggle；float/fill 覆盖预览时正文 `inert`，贴边时不 inert。 |
| `ChangeCard` | 每轮对话尾部一张（#343），只在 round 结束且 API 报告有变更时出现；header 用「已编辑 N 个文件」或单文件路径；默认 4 行、超出展开 / 收起；hover / 键盘聚焦 500ms 才取该文件摘要，浮层限高 420px；header / 行点击打开该 `(task, round)` 的 review 页签；卡片不承载完整 diff。 |
| `ChangesReviewTab` | 每个 `(task, round)` 一个页签（#343）：文件列表 + 每文件 reviewed checkbox + `N/M reviewed`、合并 / 并排（单侧变更只能合并）、换行、打开文件（复用 `FilePreviewTab`）、下载；binary / oversized 只给状态与下载；顶部固定说明「不是完整工作区 diff」；`reviewed` 只在内存中，切任务清空、换 round 用新 key；无 apply/reject。 |
| `PlanPane` | 渲染 Markdown + 标题目录；版本 / 状态徽标；**编辑在「已停下」或「失败」（B1 已放宽，见 §9）可用**；运行中点编辑给出原因而不是藏按钮；保存提示「下一条消息生效」。 |
| `EventPane` | 时间 mono + 类型词 + 摘要；不做可点假象；注明事件只做旁证、状态以快照为准。心跳（`heartbeat`）不进用户可见账本：store 只追加 `isLedgerType` 事件（当前 = 除 heartbeat 外全部），EventPane 再按同一判定过滤；Trace 仍保留原始流。 |
| `TraceFace` | 保留现役 `TraceTab` 能力（`GET /trace`，NDJSON 回放 / 导出）；入口在右栏事件页底部，打开时中栏临时全宽显示并带返回；本期不重构。 |
| `SettingsDialog` | 原生 `<dialog>`（焦点圈定 / Esc / 归还焦点）；Provider 卡片列表（名称 / ID / 地址 / 协议 / Key 状态 / 设为默认 / 编辑 / 删除二次确认）+「添加模型提供商」（第三方预设 / 自定义两页签；预设来自后端 `preset` 卡片 + 内置 OpenCode Go 模板（只预填字段、不写存储），后端没有预设卡片时不虚构；自定义页签含草稿模型目录：可搜索 / 勾选 / 全选 / 手动添加，保存时随 Provider 原子创建）；每卡模型目录（获取可用模型 = 预览勾选后加入、手动添加、逐条测试 / 删除）；条目级参数编辑（#267 / D-36：temperature / max_tokens / thinking / reasoning_effort，显式清空 = 传 null 回到继承，未改字段省略）；Key 只写不读，编辑时可清除粘贴 Key。 |

### 现役组件 → 目标组件（实施对照）

| 现役 | 目标 | 处理 |
|---|---|---|
| `TaskView` | 三栏骨架 | 重排 |
| `HistoryPanel` | `TaskRail`（左栏） | 重做（目录分组、相对时间） |
| `TaskPanel` | 中栏（对话 + composer）+ `SubagentStrip` | 拆分 |
| `TaskHeader` | `TaskHeader` | 重做（两行信息、非终态停止） |
| `InputBar` | `Composer` | 并入（模式 / 模型 / 发送） |
| `MessageStream` | 对话记录（气泡） | 重做（去轮次标签、Markdown、每轮过程折叠） |
| `StepTimeline` + `StepDetail` | `StepRow` 行内展开 | 合并，右栏不再放步骤 |
| `PlanPanel` | `PlanPane` + `PlanAnchor` | 拆成右栏页签 + 对话入口 |
| `WorkspaceFiles` | `FilePane` + `FilePreviewTab` | 保留能力，预览改为第三栏页签 |
| `SubtaskList` | `SubagentStrip` + 就地详情 | 提升到顶部条 |
| `TraceTab` | `TraceFace`（保留） | 不改组件，只换入口 |
| `ModeSwitch` / `ModelSelect` | `Composer` 内控件 | 收进输入区 |
| `SettingsPage` | `SettingsDialog`（原生 dialog） | 从整页覆盖改为对话框 |

### 状态语义与矩阵

- 枚举：`PENDING`（瞬态，展示为**启动中**）、`RUNNING`（运行中）、`INTERRUPTED`（已停下）、`FAILED`（失败）。
- **#189 规则**：界面状态只由任务快照决定；SSE 回放里的 `task_created(PENDING)` 不得覆盖已有快照；`PENDING` 展示为启动中且允许停止；停止按钮在「非终态」（启动中 / 运行中）显示。

| 状态 | 色点 | 输入框 | 停止按钮 | 发消息 = |
|---|---|---|---|---|
| 启动中 | `--text-tertiary` | 禁用 | 显示 | — |
| 运行中 | `--signal-now` | 可编辑；发送禁用 | 显示 | — |
| 已停下 | `--state-warn` | 可用 | 不显示 | 续说 |
| 失败 | `--state-danger` | 可用（B1 已放宽，见 §9） | 不显示 | 续说 |
| 未选任务 | 无 | 可用 | 不显示 | 新建 |

运行中可编辑是 #342 的有意放宽：输入框要能敲 `/stop`，也要能先写下一轮草稿；发送键与 Enter 仍不可用（`canSend=false`）。启动中 / 加载 / 加载失败保持整块禁用。

## 5. 交互规则

- **计划**：对话一行入口 + 右栏计划页；展示 Markdown 与标题目录；**不做打勾清单**（无状态源）。编辑仅在停下 / 失败（B1 已放宽 `PUT /plan`，见 §9）；运行中点编辑给出原因而不是藏按钮。保存 = 整份 PUT；提示「下一条消息生效」；发现外部改动提示「载入新版 / 覆盖保存」。可选非阻断动作「切到执行并继续」= 切模式 + 聚焦输入框，不发送。
- **模式 / 模型**：都在 composer（下一轮生效）；模型按 Provider → 模型分组，目录外模型 ID 经 `目录外模型…` 分支切换（现有任务走 `{provider, model}` 自动建 manual 条目，新建任务先加入目录再创建）；任务头只读显示模式词。不引入确认闸门（ADR 0008）。
- **停止 / 续说**：停止在任务头或 `/stop`；运行中可继续输入（`/stop` 与下一轮草稿），但发送禁用、Enter 不发消息；不设独立「继续」按钮——发下一条消息即续说；placeholder 与状态词承接语义。
- **文件**：不承诺目录懒加载、mtime、新旧标记（后端没有）；只承诺可折叠文件树（根展开、过滤按名称/路径）+ 页签式文本预览 + 下载 + 截断提示。预览 header 的树 toggle 打开 #346 navigator（420 浮动 / ≥560 贴边 / 窄屏铺满），复用同一棵树与同一轮询，不写第二棵树。预览失败按 HTTP 状态给原因与下一步（二进制 / 越界 / 不存在 / 工作区不存在），不合并成一句「无法预览」。
- **变更（#343）**：每轮尾部卡是摘要，review 页签才是 diff；变更集只含 `write`/`edit` 成功触达的文件，不列纯 shell 写入，也不代表完整工作区。`reviewed` 是会话内标记，不持久化、不上传；单侧变更自动合并视图；当前文件工具不产生删除（`deleted` 只在 shell 删除已被捕获文件时出现）。
- **历史**：默认当前目录；「全部」按目录分组、行上标目录名；行显示状态词 + 相对时间；交接提示本期不做（无后台身份数据）。删除（#241）：行内二次确认逐条删、目录分组对话框整批删；只删历史（任务后台进程一并终止），运行中拒绝。
- **工作区**：左栏 footer 显示当前目录 basename，点开原生 `<dialog>` 输入已存在目录或选最近目录（#242）；只切换历史与新建任务落点、不重启后台，`?dir=` 深链保持；不做服务端目录浏览器。
- **多轮**：对话连续排布，**不渲染「第 N 轮」标签**；每轮的用户气泡后是一条 `TurnProcess` 折叠行（已完成 / 执行中 / 处理失败 + 步数 + 工具次数），展开只显示本轮步骤（`StepRecord.round`，#265，必填）；回答平铺在过程行之后；计划入口行固定在全部轮次之后。没有 `round` 的旧记录不进前端（后端按旧格式拒绝加载，ADR 0013 决策 1）。
- **快捷键**：Enter 发送 / Shift+Enter 换行；提示只出现一处。
- **命令（#342）**：输入框的 `/` 只在开头 / 空白后 / 标点后触发，且只认光标所在 token；URL、文件路径、词中 `/` 不触发。菜单在上方、分组「命令」，按 `/` 后的前缀过滤；↑/↓ 高亮，Enter/Tab 执行，Esc 关闭并保留草稿，点击执行；焦点留在输入框，`aria-activedescendant` 指向高亮项。执行只删除 `/token`、不发送消息。无匹配显示「没有匹配的命令或技能」，Enter 仍按普通文本发送；不可用项灰显并给原因。技能组见 #345，本组件不取技能目录。

## 6. 可访问性基线（每组件验收）

- [ ] 键盘可达：Tab 顺序 = 视觉顺序；无陷阱；模态用原生 `<dialog>`。
- [ ] 页签 `role=tablist/tab/tabpanel` + `aria-controls` + ←/→/Home/End。
- [ ] 可见焦点：`:focus-visible` 2px 信号环、offset 2px；不出现裸 `outline:none`。
- [ ] 名称：图标按钮 `aria-label`；表单控件有 label 或 `aria-label`；装饰 SVG `aria-hidden`。
- [ ] 状态与通知：状态 `aria-live=polite`；toast `role=status`；错误可读原因与下一步。
- [ ] 目标尺寸 ≥24×24；点击目标 `touch-action: manipulation`。
- [ ] 对比度：正文 ≥4.5:1、大字 ≥3:1、控件 ≥3:1；深色主题设 `color-scheme: dark`。
- [ ] 动效：只动 transform/opacity；`prefers-reduced-motion` 下停循环与位移。
- [ ] 跳转链接到对话记录；页面唯一 h1。

### 窄屏抽屉手测清单（P1-1 / #257，D-27）

断点：`width < 860` 左栏成抽屉、`width < 1180` 右栏成抽屉；860 / 1180 本身仍是宽屏。

- [ ] 860 / 1180 恰好宽屏：无抽屉开关、无遮罩，两栏为静态布局。
- [ ] 窄于 860 打开左抽屉：开关出现，打开后焦点落在抽屉内首个可聚焦元素（关闭按钮）；右抽屉窄于 1180 同理。
- [ ] 抽屉内 Tab / Shift+Tab 循环（含首尾回绕），焦点不逃出抽屉。
- [ ] Esc 关闭抽屉，焦点回到触发它的开关。
- [ ] 抽屉打开时背景（对话列、另一侧栏、跳转链接）Tab 不可达（inert）。
- [ ] 关闭抽屉后背景恢复可交互：对话列 / 另一侧栏可点击、可 Tab、读屏可见（`inert` / `aria-hidden` 已撤销）。
- [ ] 窗口从窄变宽：遮罩与抽屉一起消失，无孤儿遮罩。
- [ ] 打开右抽屉后点「打开完整 Trace」：抽屉收起，遮罩不盖 Trace 面。
- [ ] Trace 打开时不显示左右抽屉开关，关闭 Trace 后恢复。
- [ ] 读屏播报抽屉为 dialog：名称「任务列表」/「工作区与文件」，`aria-modal=true`。
- [ ] 仅用 Tab / Shift+Tab / Esc 完成「开 → 用 → 关」全流程，焦点不丢。

### P1-2 手测清单（#258）

- [ ] 400 字符 token / 2000 字单词 / 长 URL / CJK+emoji：对话与计划页均按字符断行，无整页横向滚动。
- [ ] 工具调用展开后，超长入参出现「展开全部 / 收起」，展开后不再受 `max-h` 限制（仍可横向滚动）。
- [ ] 事件页无 heartbeat 条目；Trace 回放（打开完整 Trace）仍能看到原始流。
- [ ] DevTools Network：选中已停下任务只发一次快照 GET（无并列第二次 `getTask`）；长任务运行期间无 list GET 风暴。
- [ ] 流式回答期间 Markdown 持续更新，结束后不再无谓重渲染（React DevTools Profiler 抽查）。
- [ ] 发送失败（断开后台）显示「无法连接后台；确认服务在运行后再试」，读屏有播报（`role=status`）。
- [ ] 文件面板失败显示原因 + 下一步，「重试」可用；加载 / 空 / 失败状态读屏可感知（`role=status`）。
- [ ] 运行中任务单次 poll 失败（列表已有文件）→ 仍显示原因与「重试」，读屏播报原因。
- [ ] 对比度：`text-state-danger` / `text-tertiary` 在 `bg-surface-page` / `bg-surface-well` 上仍达 §1 标准（本片未改 token）。

注（已知窄竞态，接受）：缓存任务为 RUNNING 且 SSE 回放先于快照返回时，Network 可能出现 2 次 GET；改动前同存，不作为回归门槛。

### P2-1 Composer 拆分手测清单（#232，D-32）

拆分为 `lib/composerState.ts`（`composerView` / `clearedDraft`）+ `hooks/useComposerSend` + `hooks/useComposerControls`；行为、逐字文案、aria 与禁用规则均不变，下列为 hooks 接线的走查项。

- [ ] 新建任务：输入后 Enter / 发送 → 创建成功进入该任务；发送中按钮禁用；只有「已发出」的草稿被清空（发送中继续输入的文字保留）。
- [ ] 已停下任务续说：消息进入同一对话（同 thread_id / checkpoint）；失败时草稿不丢且显示服务端原因。
- [ ] 状态矩阵：未选任务 / 加载中 / 加载失败（401 与非 401）/ PENDING / RUNNING / INTERRUPTED / FAILED 的禁用原因与 placeholder 与拆分前逐字一致（`composerState.test.ts` 钉文案，走查确认渲染）。
- [ ] 模式切换：未选任务只改草稿；已有任务走 `PUT` 并刷新快照；运行中显示「下一轮生效」；断网显示「切换失败：无法连接后台；确认服务在运行后再试。」。
- [ ] 模型切换：同上；选择当前模型时不发请求。
- [ ] 切换任务：上一条任务的 sendError / switchNote 不带到新任务（reset effect）。
- [ ] Enter 发送 / Shift+Enter 换行 / 输入法组合中（isComposing）Enter 不发送。
- [ ] 读屏：底部原因行 `role=status` 可播报；`textarea` / `select` 的 `aria-label` 与 `aria-describedby` 与拆分前一致。

### P2-3 Provider 设置与 Composer 模型切换手测清单（#243，D-37）

机械面由 `lib/modelCatalog.ts` 的纯函数断言覆盖（分组 / 默认动作 / 表单校验 / 发现预览 / 失败文案）；下列为组件接线与真实网络走查项。

- [ ] 设置页按 Provider 成卡：名称 / ID / 地址 / 协议 / Key 状态（已设（粘贴|环境变量）/ 未设）可见（ID 为小号等宽文本，同时进入卡片操作按钮的可访问名称）；首次打开加载失败时显示原因与「重试」按钮（后台恢复后点击继续）；DevTools Network 看 `GET /providers` 与 `GET /providers/{id}/models`，响应只有 `key_set` / `key_source`，任何响应与页面都不出现 Key 明文。
- [ ] 新建（自定义模型 API）：填 ID / 名称 / 地址 / 协议 / 环境变量名 / Key；ID 大写或数字开头、地址非 http(s)、环境变量名非法时字段错误可读（原因 + 下一步）；HTTP 400 服务端原因原样显示不吞。
- [ ] 新建协议选 Anthropic 并保存：创建 / 编辑成功（非流式适配器已可用，卡片协议显示「Anthropic 原生（非流式）」）；提示文案明示两项剩余限制「模型目录发现暂不支持；thinking / reasoning_effort 对 Anthropic 暂不支持」。已有 anthropic 卡可改名称 / 地址 / 环境变量 / Key；服务端对不支持的参数组合给出可读 400 时原样显示原因。
- [ ] 新建（第三方模型供应商）：后端有 `preset` 卡片时列出（如「默认配置」），选中自动带地址 / 协议 / 环境变量；没有预设时给出「改用自定义」下一步；未选预设时创建按钮禁用并说明原因。
- [ ] 新建（OpenCode Go 内置模板）：预设页签出现 OpenCode Go；选中后切到自定义表单并填好 Provider ID / 名称 / 地址 / 协议 / 环境变量名（均可改）；已有同 id Provider 时入口隐藏并说明原因。填 Key 后「获取可用模型」给出可搜索 / 可勾选候选（默认全选），全选 / 取消全选 / 手动添加可用；保存前关闭或切页签不产生 Provider、模型或凭据；保存后 Provider 与勾选模型一次出现；发现失败仍可手动添加并保存。
- [ ] 编辑 Provider：ID 只读；改名称 / 地址 / 协议生效；写 Key 只写不读（保存后状态变已设）；粘贴 Key 的「清除已保存的 Key」二次确认后回退到环境变量 / 未设。
- [ ] 删除 Provider：卡上「删除」需二次确认，确认态明文说明「会级联删除该 Provider 下全部模型条目与 Key；引用它的任务下一轮会失败」；最后一模型的 Provider 删除被 409 拒绝并显示原因与下一步，不破坏其余卡。
- [ ] 设为默认：点某 Provider 卡的「设为默认」→ 该卡出现「默认」标记、其首条目在目录行标「默认」，原 Provider 的标记消失；刷新 / 重开设置后标记保持。该 Provider 已是默认时按钮禁用并说明原因；目录为空时按钮禁用并提示先添加模型。
- [ ] 模型目录：手动添加模型 ID / 显示名；空 ID、重复 ID 错误可读；Enter 连按只发一次请求；逐条「测试」能给出连接结果；逐条删除需二次确认（确认态说明引用它的任务下一轮会失败），目录最后一条被 409 拒绝并提示。
- [ ] 获取可用模型：Key 错 → 「认证失败（HTTP 401）…」；地址错 → HTTP / 超时原因；两者都带「也可以手动添加模型 ID」；预览勾选后点「加入目录」才落库。**关闭/重开残留**：获取预览后不加入 → 关闭设置 → 编辑该 Provider 的地址 / Key → 重开 → 旧预览、删除确认、添加表单与错误提示均不出现（临时状态随会话清空）。Anthropic 卡的「获取可用模型」禁用并给出手动添加提示（服务端对 anthropic 的发现请求仍返回「协议暂不支持发现」，页面显示原因与下一步）。
- [ ] Composer：`<select>` 按 Provider `<optgroup>` 分组；切换模型后任务持久化（刷新仍在）；运行中切换显示「下一轮生效」，停止 / 失败态切换成功显示「已切换」；切换任务后 switchNote 不串。
- [ ] Composer 旧任务：`Task.model` 为条目 id 时显示条目名称；条目已被删 / 目录外时回退显示原始 id 且仍可发送（不静默改投默认模型）。
- [ ] Composer 目录外模型：现有任务选「目录外模型…」，填 Provider + 裸模型 ID → 切换成功、自动进目录、Composer 出现该条目；新建任务同路径先加入目录再创建；切换失败显示原因 + 下一步（Provider 地址 / Key / 协议）；断网显示「无法连接后台」。
- [ ] 读屏 / 键盘：模型 select、目录外 Provider / 模型输入、发现与加入目录复选框均有 `aria-label`；卡片按钮名称在 Provider 间唯一（含模型行的测试 / 删除）；模型切换结果（含「下一轮生效」与失败原因）与设置页加载态走 `role=status`，Provider 卡片目录操作错误走 `role=alert`；Tab 顺序不跳出对话框，Esc 只关最上层对话框。

### P2-4 思考展示与参数入口手测清单（#267，D-38 / D-40）

机械面由 `lib/reasoning.ts`（空 / 缺省不渲染）、`lib/stepSummary.ts`（最终回答步骤摘要不重复回答正文）、`lib/modelParams.ts`（tri-state 映射、区间校验、只提交改动字段、继承提示）的纯函数断言与 `store/reduceTask` 的 `final_answer.reasoning` 断言覆盖；下列为组件接线与真实模型走查项（本片只做条目级参数，Provider 级默认以继承提示展示、不提供编辑；**Provider 级默认编辑留后续片（#267 后续 / 参数入口二期）**）。

> **前置条件（已满足）**：两条依赖后端持久化的走查项（「刷新页面 / 重新选中任务后思考仍在」「思考随停下后的快照出现」）依赖 **P2-4a（PR #301，master 1505f49 已合入）**；合入前停靠快照没有 `reasoning` 字段时这两条会显示「无思考块」，不代表前端回归。

- [ ] 思考展示：用思考型模型跑完一轮 → 展开该轮过程 → 展开步骤 → 「思考」块默认折叠；点开显示思考正文；步骤行头出现「思考」机器事实词。
- [ ] 非思考模型 / 旧任务（无 `reasoning` 字段）：步骤展开区不出现「思考」块，也没有空块或占位。
- [ ] 刷新页面 / 重新选中任务后思考仍在（来自持久化快照）；运行中不显示半截思考（思考随停下后的快照出现，事件里的脱敏 reasoning 只做即时补充）。
- [ ] 长思考：超长文本按字符断行、块内限高滚动，不把对话列撑出横向滚动；`prefers-reduced-motion` 下折叠动画不位移。
- [ ] 回答面（`AnswerSheet`）与 `TurnProcess` 汇总行不重复显示思考。
- [ ] 最终回答步骤行显示「最终回答」，不重复回答正文；思考仍只在独立折叠块。历史任务同样如此（`step.thought` 与 `final_answer` 同文时，不再把回答正文塞进行首摘要）。
- [ ] 参数编辑：设置 → 模型目录行「参数」→ 表单初值来自条目原始覆盖值；未设置字段显示「继承 Provider 默认 / 内置默认」；Anthropic 卡给出 thinking / reasoning_effort 限制提示。
- [ ] 只改一个字段保存：DevTools Network 的 `PUT /providers/{id}/models/{model_id}` 请求体只含该字段；保存后刷新设置 / 刷新页面值仍在。
- [ ] 清空数值（如 temperature）保存：请求体该字段为 `null`（回到继承）；表单提示重新显示继承值，而不是把 Provider 默认误当条目覆盖。
- [ ] 非法值：temperature 非数字 / 超范围（OpenAI 兼容 0–2、Anthropic 0–1）、max_tokens 非正整数 → 字段错误可读且不发请求。
- [ ] 协议限制：Anthropic 卡把 thinking 设为「开」或 reasoning_effort 设为 low/medium/high 保存 → 服务端 400 原因（含下一步）原样显示；thinking 设为「关」/ 清空 reasoning_effort 可正常保存（后端只拒「开 + 非空」组合）。前端不静默拦截、不留下半套配置。
- [ ] 读屏 / 键盘：步骤「思考」折叠按钮有 `aria-expanded` / `aria-controls`，展开与收起时名称分别读作「展开 / 收起模型思考」；参数表单控件有 `aria-label`，字段错误经 `aria-describedby` 关联，保存中字段禁用，保存成功 / 失败走 `role=status` / `role=alert`；Tab 顺序不跳出参数表单。

### P4-2 能力包与预设手测清单（#67，P4-2）

机械面由 `lib/plugins.ts`（三态 ↔ PUT body：跟随 = overrides 省略该键；预设切换保留显式覆盖；dirty 判定；active 面摘要）与 `store/pluginsStore.ts`（加载 / 保存 / 失败保留旧快照 / in-flight 守卫）的纯函数与 store 断言覆盖；下列为组件接线、真实后端与重启语义的走查项。

- [ ] 设置页 →「能力」区加载：预设三选一（最小 / 研究 / 完整）可见；每包一行显示 id、工具数、MCP / 子代理面标记、三态选择（跟随预设 / 强制开 / 强制关）；「当前生效（后台已加载）」显示能力包与工具名清单（来自 GET 的 active，前端不自行推导）；已保存与生效面不同时显示「重启后台后生效」提示。
- [ ] **保存 → 重启后台 → 生效面变化**：某包「强制关」→ 保存（按钮短暂「保存中…」并禁用）→ 页面提示重启；**不重启**时 active 面与工具清单不变；重启后台并重新打开设置 → 该包从 active 面消失、工具清单少对应工具；反向「强制开」在 minimal 预设下重启后出现该包工具。
- [ ] **三态 override 实际工具面差异**：对同一包分别尝试「跟随预设 / 强制开 / 强制关」→ 保存并重启后，active.packages 与 active.tools 逐项符合三态语义；切回「跟随预设」保存后 PUT body 的 overrides 中不再含该包 id（Network 检查）。
- [ ] 预设切换后显式覆盖保留：先把某包设为强制关 → 切到另一预设 → 保存 → PUT body 同时含新 preset 与该包 override；重开设置后三态仍是强制关。
- [ ] 错误 / 禁用态：断开后台时保存 → 错误文案可读且 role=alert，旧快照仍在、保存按钮可重试；非法响应 / 4xx 服务端原因原样显示；保存中预设与三态不可再改（避免未提交输入被当成已保存）。**保存中：错误横幅的「重试」按钮禁用并显示「保存中…」（含 title 说明）。**
- [ ] 窄屏 / 长工具名：窄窗下工具清单与三态行换行、不撑破设置对话框；超长工具名不产生横向滚动（可断行）；`prefers-reduced-motion` 下无动画位移。
- [ ] 读屏 / 键盘：能力区为 `fieldset + legend`；每个能力包 select 有绑定 label，预设组有 aria-label；重启提示与保存结果走 `role=status`，错误走 `role=alert`；仅用 Tab / 方向键即可完成「切预设 → 改三态 → 保存」。

### 续跑轮次头手测清单（#287 返工 / PR #317 F2）

机械面由 `lib/rounds.ts` 的 `turnVerdict` 纯函数断言覆盖（轮次结论取末步；`live` 时运行中优先，末步已失败仍显示失败；`some(running)` 仅作启动收尾前旧数据的兜底）；下列为组件接线与真机走查项。

- [ ] 硬杀后续跑：第一轮的断点步骤收为「失败」红点行；首个新步骤落盘后轮次头显示「执行中」，续跑产出回答后显示「已完成」，不再一直停在「处理失败」（首个新步骤前仍会短暂显示「处理失败」）。
- [ ] 正常未中断轮次标签不变：实时运行显示「执行中」；报错 / 停止且末步失败显示「处理失败」。

### 回放不翻倍工具调用手测清单（#287 retro / 2026-11-09）

机械面由 `store/reduceTask.test.ts` 的「tool_call 回放归属与幂等」断言覆盖（回放已知调用留在原步骤、不把收尾步骤翻回 `running`；同一事件缓冲区回放两遍快照逐字段不变）；下列为组件接线与真机走查项。

- [ ] 重开已停下任务：在一个有多轮、多工具调用的任务上刷新页面，轮次头保持「已完成」而非「执行中」，各步骤的工具调用条数不翻倍；重新选中同一任务后仍一致。
- [ ] 运行中打开任务：新工具调用照常落到当前步骤并显示运行中；旧调用的 `tool_call` / `tool_result` 回放不改变已收尾步骤的状态。

### S2 当前合同手测清单（#329）

机械面由 `store/reduceTask.test.ts` 的「缺 round 的 step_start 是坏帧」「无步骤 tool_call 不造桩」断言覆盖；下列为真机走查项。

- [ ] 把任务记录改成旧格式（缺 `workspace` / `round=0`）→ 后台明确拒绝，网页显示失败而不是渲染伪造轮次或旧目录。
- [ ] 运行中收到无对应步骤的 `tool_call` 回放 → 页面不凭空生成步骤或轮次头，随后的 REST 快照能正常显示真实步骤。

### S8 Trace 模型调用与合并策略手测清单（#334）

机械面由 `lib/modelCalls.test.ts`（占位/最终记录按 attempt 去重、重放不翻倍、`truncated`、失败记录、畸形帧）与 `store/reduceTask.test.ts` 的「S8 显式修订」组（快照/事件/续说按 `(round, step, plan_version)` 判新旧、旧轮 park 不停止新轮、相同文本多轮、迟到状态响应）覆盖；下列为真机走查项。

- [ ] 跑一个含工具往返的任务，打开「完整 Trace」→ 模型调用区列出每次调用的用途、模型、状态、`call_id` 与记录完整性；「模型」筛选计数与列表一致。
- [ ] 展开一条调用 → Network 出现一次 `GET /tasks/{id}/trace?type=llm_call&call_id=...`，面板显示请求/响应/错误/usage；收起再展开不重复堆记录。
- [ ] 请求超过 128 KiB 的调用显示「请求预览已截断」；记录仍可读，导出 `.jsonl` 仍是无参完整 trace。
- [ ] 任务停下后刷新页面、重启后台再打开 → 模型调用仍可读；删除任务后 Trace 显示「暂无 Trace 回放」且网络请求为 404。
- [ ] 运行中触发一次 SSE 重连（DevTools 离线/恢复）→ 不出现旧轮「停下」把新轮翻停、状态与步骤不倒退；控制台无报错。
- [ ] 模型返回的文本/请求体按纯文本展示（含 `<script>` 字样时不执行），长内容可滚动。

### S9 文件轮询手测清单（#335）

机械面由 `lib/filePolling.test.ts` 覆盖（慢响应期间不安排下一次、请求结束后才排下一次、stop 清定时器并丢弃在途结果、失败也算结束并重排、重启后代次隔离）；下列为真机走查项。

- [ ] 运行中任务打开文件面板，用 DevTools 把网络调慢：同一时刻最多一条 `/tasks/{id}/files` 在途，后一条只在上一条结束后约 2 秒出现，不按固定定时器堆积。
- [ ] 慢响应期间切到另一个任务：旧任务的迟到响应不覆盖新任务的列表；隐藏 / 关闭面板后不再新增 `/files` 请求。
- [ ] 让 `/files` 返回 500：面板显示原因与「重试」；点击重试立即发起一次请求，错误消失后列表恢复。任务停下后触发一次终态刷新，随后不再轮询。
- [ ] 正常列表、空目录与「超过 2000 个」截断提示与改动前一致。

### Changes 卡与 review 页签手测清单（#343）

纯函数 / store 面由 `lib/changes.test.ts`、`lib/dashTabs.test.ts`、`store/changesReviewStore.test.ts` 覆盖；下列为真机走查项。

- [ ] 含 `write`/`edit` 的 round 停下后，对话尾部出现卡：单文件显示文件名，多文件显示 `已编辑 N 个文件`；纯 shell 改的文件不出现；无变更的 round 不出卡。
- [ ] 超过 4 个文件默认折叠，展开 / 收起计数正确；header 与每行的增删计数、状态词与后端一致。
- [ ] hover 或键盘聚焦文件行 500ms 后出现 unified 预览，高度不超过 420px；快速移开不闪出迟到预览。
- [ ] 点 header / 行打开右栏 review 页签并选中正确文件；同一 `(task, round)` 再点只复用 / 切换，不重复开页签；关闭页签回到相邻页签或文件列表。
- [ ] review 页签：文件列表、每文件 reviewed checkbox 与 `N/M reviewed` 生效；切换 round 后标记从 0 开始；切换任务后全部清空。
- [ ] reviewed checkbox 的命中区至少 24×24（label 包裹），点勾选不误触文件选择按钮。
- [ ] 合并 / 并排可切；新增 / 删除文件只能合并；换行开关改变长行呈现；diff 区内部滚动，420px 下页面不横向溢出。
- [ ] 「打开文件」复用已有文件预览页签；下载走现有 `/files/download`；binary / oversized 行显示状态、无 diff、仍可下载。
- [ ] 页签顶部能看到「不是完整工作区 diff」说明；界面没有 apply / reject。
- [ ] 读接口失败（404 / 400 / 500 / 断网）给出原因与下一步，不伪造空变更集。

### 文件代码预览手测清单（#344）

机械面由 `lib/codePreview.test.ts` 覆盖（扩展名 → 语言、CRLF/末尾换行、2000 行上限、截断提示、行号模型、空文件文案、深路径面包屑折叠）；下列为组件接线与真实浏览器走查项（D3/D6/D8：Shiki 按需高亮，未退 highlight.js、未引 Monaco）。

- [ ] 每种语言各开一个文件：`ts/tsx/js/jsx/json/md/py/sh/css/html/yaml/toml` 均出现语法着色；未知扩展名（如 `.txt`）显示纯文本且无高亮请求。
- [ ] 首次预览才加载高亮 chunk（DevTools Network 在预览前无 Shiki chunk）；把某个语言 chunk 请求置为失败后重开预览，正文仍以纯文本显示，不白屏、控制台无未捕获异常。
- [ ] 头部：面包屑目录段 + 文件名、大小、下载；深路径显示为 `…/最近目录/文件名`，`title` 仍是完整路径；420px 下头部与整列无横向滚动。
- [ ] 行号从 1 开始、与代码行逐行对齐；行号列 `aria-hidden`，选中正文复制只得到代码、不含行号（可复制一段粘贴到编辑器验证）。
- [ ] 长行只在正文内横向滚动；横向滚动时行号 gutter 固定在左侧；`tab-size: 2`（含 tab 缩进的文件不撑成 8 列）。
- [ ] 空文件显示「空文件」空态；CRLF 文件不出现 `^M` 或多余空行；末尾换行不额外多一行。
- [ ] 超过 200KB 的文件：提示「文件超过 200KB，内容已截断；完整内容请下载。」；超过 2000 行显示「仅显示前 2000 行，完整内容请下载。」，两者同时命中合成一句；正文最多 2000 行。
- [ ] 二进制（415）、404 文件不存在、404 任务不存在、409 工作区不存在、500、网络断开各有可区分的原因 + 下一步；每种状态下载入口都在，不白屏。
- [ ] 同一文件不重复开页签；关闭/切换页签与焦点行为不变；页签标题带文件图标；预览全程只读。
- [ ] 键盘 / 读屏：Tab 可到页签、下载与正文滚动区；正文滚动区为 `role=region` + `aria-label`，聚焦有 2px 信号环，方向键可横向滚动；加载 / 截断提示可被读屏播报。

### Files 浮动树手测清单（#346，布局 C）

机械面由 `lib/navigatorLayout.test.ts`（行宽 399/400/420/559/560 与视口 767/768 的模式、默认/最小/最大宽度、clamp、键盘步进）与 `lib/navigatorState.test.ts`（按 task 隔离）覆盖；下列为组件接线与真实浏览器走查项（D-23/D-24：不引 jsdom）。

- [ ] 预览 header 出现树 toggle；打开/关闭不切换预览 tab，也不重置树的展开、筛选与滚动位置。
- [ ] 420 行宽（桌面视口）：树为浮动面板，默认 260；拖拽 / ←→ 可到 180–300，Home/End 到最小/最大；预览仍按整宽排版，不被永久挤窄。
- [ ] 400 / 559 行宽仍是浮动（最大 280 / 300），560 起为贴边 raised，预览 body ≥320；399 或视口 767 铺满，视口 768 回到浮动；各档无横向溢出。
- [ ] 浮动面板不盖住 header 的面包屑、大小、下载与 toggle；打开文件后这些操作仍可直接使用。
- [ ] 贴边模式：树与预览并排（非硬分栏），拖动分隔条时 body 仍 ≥320；树与正文都可 Tab 到达。
- [ ] 铺满模式：树在 header 下方铺满；点文件后树自动收起并显示预览；再点 toggle 回到树；`row < 400` 与视口 `< 768` 两条件命中同一行为。
- [ ] 拖拽 + 方向键调整宽度后，切换预览 tab 再回来、切换任务再回来，宽度与开合保持（按 task 记忆）；展开/筛选/滚动沿用 #341，切任务时列表态按 #341 重置。
- [ ] 打开树：焦点落在树内首个可见行（列表未加载时落容器）；↑/↓/Home/End/Enter 与 #341 一致；Esc 关闭并回 toggle；Tab 不被困在树里。
- [ ] float/fill 打开时，被覆盖的预览正文 Tab 不可达（`inert` / `aria-hidden`），关闭后恢复；贴边模式正文不 inert。
- [ ] 420px 截图/录屏留证（`.agents/temp/`，不入库）；无全屏、无硬分栏、无第二棵树/第二套预览。

### Files 文件树手测清单（#341）

机械面由 `lib/fileTree.test.ts` 覆盖（树构建 / 自然排序 / 展开折叠 / 过滤与祖先展开 / 清空恢复 / 键盘转移）；下列为真机走查项。

- [ ] 根目录文件可见；展开多层目录后缩进逐级正确，折叠后子树消失；目录排在文件前、名称自然排序（`file2` 在 `file10` 前）。
- [ ] 用键盘走完「Tab 进树 → 上下移动 → 右展开 → 左折叠/回父级 → Enter 打开 → Home/End」；焦点环可见，Tab 能离开树，无键盘陷阱。
- [ ] 过滤命中名称与路径两种；命中分支自动展开、无关分支消失；无结果显示文案；清空后回到过滤前的展开状态。
- [ ] 运行中任务连续观察 3 次轮询：展开状态、滚动位置与当前预览文件都不丢；切到另一任务后树、过滤、滚动和预览都重置。
- [ ] 420px 右栏（含深路径与超长文件名）：不出现横向滚动，文件名截断且 `title` 可看全路径；下载入口仍可点。
- [ ] 窄屏抽屉关闭时不发 `/tasks/{id}/files` 请求，重新打开立即加载一次。

### 输入框 / 命令菜单手测清单（#342）

机械面由 `lib/slashCommands.test.ts` 覆盖（触发边界、URL/路径/词中不触发、前缀过滤、8 命令可用性、↑/↓ 回绕、删 token）；下列为真机走查项。

- [ ] 在空输入、空格后、换行后、中文标点后输入 `/` → 菜单弹出；在 `https://example.com`、`src/main.ts`、`foo/bar`、`/usr/bin` 中输入或把光标放到 `/` 后 → 不弹出；光标离开 token（如 `/new ` 后）→ 菜单关闭。
- [ ] 输入 `/` 显示 8 条内置命令与分组「命令」；技能组按 #345 清单单独走查（`skills` 包关闭时显示空态）。输入 `n` / `mo` / `zzz` 分别只剩 `new` / `model` / 「没有匹配的命令或技能」（`zzz` 时技能组不占位）。
- [ ] ↑/↓ 循环高亮；Enter 与 Tab 执行高亮项；Esc 关闭并保留草稿；鼠标点击可执行且焦点仍在输入框；`aria-activedescendant` 随高亮变化。
- [ ] 无匹配时按 Enter：文本作为普通消息发出（不吞输入）；Esc 后草稿原样保留。
- [ ] 8 条命令逐个执行：`/new` 回新任务并清空草稿；`/stop` 只对启动中/运行中任务可用；`/plan` `/build` 走现有模式切换；`/model` 聚焦模型下拉；`/resume` 聚焦输入框并提示「输入下一条消息即可继续」且不发消息；`/tasks` 在窄屏打开左抽屉；`/help` 打开命令说明。
- [ ] 运行中可输入 `/stop`，发送仍禁用：RUNNING 任务输入框可编辑、文字保留；发送按钮禁用且 Enter 不发消息；执行 `/stop` 后 `/token` 被删除、状态随后变为已停下。启动中（PENDING）输入框仍禁用。
- [ ] 不可用项灰显且带原因（已在计划模式 / 已在执行模式 / 没有运行中的任务 / 没有可继续的任务 / 没有可选模型）；执行任一命令后 `/token` 被删除，对话不新增消息。
- [ ] 420px 宽：菜单不超出输入区，长命令/原因截断不撑破布局；帮助对话框可用。

### 技能目录 / 技能菜单手测清单（#345）

机械面由 `lib/skillCatalog.test.ts` 覆盖（同名合并 / 层标签 / 名称与描述过滤 / 包关闭空态 / 目录失败原因 / 引用插入）；下列为真机走查项。

- [ ] 在工作区 `.agents/skills/<dir>/SKILL.md` 与 home `~/.agents/skills/<dir>/SKILL.md` 各放一个技能；输入 `/` 出现「技能」组，行内显示名称、描述与来源层（home / workspace）。
- [ ] 按名称片段与描述片段过滤技能（如 `pdf`、`SPLIT`）；无匹配技能时命令仍可过滤/执行，`zzz` 仍显示「没有匹配的命令或技能」。
- [ ] 同名技能在 home 与 workspace 各有一份：菜单只出现一行，来源标「home + workspace」；选中后草稿只插入 `使用技能「name」：`（不带层名），发送后 Trace 中 `load_skill` 返回 home 与 workspace 两份内容。
- [ ] 技能插在 `/token` 的位置：前后文字保留，光标落在 `：` 之后；`Enter` 发送的是普通用户消息，不新增命令副作用；Esc / ↑↓ / Tab / 点击行为与 #342 一致。
- [ ] 在设置 → 能力关闭 `skills` 包并重启后台：输入 `/` 时技能组显示「技能能力包已关闭；可在 设置 → 能力 打开」空态，8 条内置命令照常可用。
- [ ] 目录读取失败（如停后台、过期 401、工作区被删）：原因只出现在技能组内，带「重试」行；↑/↓ 可高亮重试并 Enter，恢复后技能列表回填；内置命令全程可用。
- [ ] 420px 宽：技能名称 / 描述 / 层标签截断且 `title` 可看全；技能组不把输入区撑破。

## 7. 空 / 错 / 加载

- 空（未选任务）：一句 21px 邀请 + 一行副说明；composer 可用。
- 空（列表 / 文件 / 计划 / 事件）：说明「这里为什么空」+ 下一步动作；不渲染破 UI。
- 错：说原因 + 下一步（失败可续说；或新建任务重来）；不道歉、不模糊。
- 加载：占位文案以「…」结尾；不用假进度（计划不打勾、任务不显示假进度条）。

## 8. Do / Don't

**Do**：用记录与行组织信息；把机器事实交给 mono；让状态文字与色点同时出现；禁用时解释原因；让回答正文直接承担成品。

**Don't**：中圆点串元信息；把信号蓝当选中的颜色；把一切做成同圆角同阴影的卡片；在自由 Markdown 上猜进度；用回放事件改写状态；为「看起来忙」造动画或假进度。

## 9. 需要后端支持的开放项（实现前先开票）

1. **失败可续说 / 可改计划**：**2026-10-27 B1（#219）已落地**——`POST /tasks/{id}/messages` 与 `PUT /tasks/{id}/plan` 接受 `FAILED`（续说需 checkpoint；`resume` 仍只接受 `INTERRUPTED`），`architecture.md` §3.3 已同步。
2. **计划 / 模式 / 模型变更事件**：三个 setter 不发事件，多标签页会脏；需 `plan_updated` 类事件或轮询。
3. **文件面增强**：目录级列表、mtime、新增标记。
4. **任务列表摘要投影**：现在返回完整 Task（含 steps / conversation），长列表重；需要摘要字段。
5. **子智能体数据**：token / 耗时 / 内部过程不暴露；界面只展示名字 / 状态 / 摘要 / 产物。
6. **交接提示**：无后台身份数据；要做得加 CLI `?handoff=1` 或 `/health` 只读字段。
7. **千级列表**：前端必须虚拟化；后端配合分页 / 计数更佳。

## 10. 验收判据

- 机械：`npm run typecheck`、`npm run build`；动 SSE 事件面时加 `npm run check:sse`。
- 走查：1920 与 1440 两档宽度；键盘完成主要操作；`prefers-reduced-motion` 下无循环与位移。
- #189 回归：运行中触发 SSE 重连，头部不出现「等待中」、停止按钮不消失。
- 真模型场景：`manual-verification.md` 中 UI 相关项（2 / 3 / 6 / 7）网页端重跑并追加结果。

### 10.1 前端测试与手测纪律（2026-11-07，P0-4 / #282）

- **拍板：本批不引入 jsdom / @testing-library（D-23）**。可复核理由：jsdom 没有布局与 `inert` 语义，焦点 / 命中 / 滚动断言只能断属性；需要为 `matchMedia` / `scrollIntoView` / `showModal` 等打 stub；新增依赖会改 `package.json` + lockfile，CI frontend job 变重；依赖变更会牵动主检出安装，跨分支锁文件漂移时 worktree 还需重建依赖。**成本口径取 D-23 的「评估」分支**：不引入即不做安装实测，避免 lockfile 污染与 CI 重跑（#282 裁定）。
- **替代与纪律（D-24）**：状态派生抽成纯函数（`lib/failureStates` 等），交互断言落在 zustand store 上；每片保留一条手测 checklist。**新增 / 修改状态面孔或组件接线的 PR 必须带一条纯函数或 store 断言 + 一条手测 checklist 项**；纯布局 / 视觉改动可只手测。
- **D-24 术语（可机械判断）**：**状态面孔** = 组件按「状态」渲染的那个分支（空 / 错 / 加载 / 失败 / 禁用 / 进行中）；**组件接线** = 把 store 或派生值接到渲染分支的那段代码（选择器、条件渲染）。
- **D-24 正反例**：正例（必须配一条纯函数或 store 断言 + 一条手测项）：改失败脸文案或判定、把 `listError` 接到横幅、改空态 / 加载态出现条件；反例（可只手测）：纯间距 / 颜色 / 布局 / 图标形状等视觉改动。
- **store 级样板**：`frontend/src/store/taskStore.test.ts` 的「D-24 接线断言模板（#282）」——用 store action 写入后再经 `lib/failureStates` 的组合判定断言「列表 500 + 零行 = 失败而非空态」（样板只钉 store → 判定这一层；组件是否消费该判定由手测兜底）。

## 11. 指针（先读哪份）

- 需求与范围：#54（含 2026-10-23 校准段）。
- 运行时与契约：[architecture.md](architecture.md) §3.3（REST）/ §3.4（SSE）/ §3.5（错误口径）。
- 模式与状态机：[ADR 0008](adr/0008-single-loop-plan-build-modes.md)、日常形态 [ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)。
- 网页验收：[manual-verification.md](manual-verification.md)（10 场景）。
- 原型：历史存档在分支 `prototype/frontend-redesign`（交互语义已过期，仅视觉参考）；本轮会话稿不入库。**实现以本文与 #54 为准**。

## 12. 变更记录

- 2026-10-23：创建。来自 #54 设计 grilling；原型经六个前端设计 skill（frontend-design / web-design-guidelines / accessibility / break-ui / emil-design-eng / performance）打磨后沉淀。
- 2026-10-23：按 PR #192 评审修订：`PUT /plan` 放宽列入 §9、provenance 来源点名、Trace 归宿（`TraceFace`）、状态改为「未交付」+ 现役→目标组件映射、发送键对比度（深色字形）、ToolRow 三值、状态色点列、纸面口径（对话框不算纸面）、#54 旧 token 作废、字体/Markdown 依赖登记、原型指针改指存档分支。
- 2026-10-23：按 PR #192 第二轮复审修订：PlanPane 只给「已停下」编辑（失败待 §9）、SubagentStrip 状态词归 Inter、机器事实改短 id + 产物数、AA 旧值依据纠正（`#63676D` 不足、`#9BA0A6` 为统一色阶）、`--text-on-signal` token、`TaskRail` 组件规则、`--state-ok` 用途收窄、ToolRow 改「终态三值」、依赖措辞改「兜底（非等价替代）+ 实施 PR 补记录」、AnswerSheet 补「空则全局默认」。
- 2026-10-27：按 [`frontend-redesign-plan.md`](specs/frontend-redesign-plan.md) 分片定稿：实施指针从「#54 四期」改为 F1–F9 + B1；依赖状态更新为已批；`--radius-control: 6` 待 F2 补入 §1；D1–D11 实现裁决见计划 §4.2。
- 2026-10-27：**已交付**（F1–F9 + B1 全部合入）。实际落地与本文的差异只在实现细节：计划页编辑含失败态（B1）、事件页时间用到达时间（SSE 帧不带 ts）、文件预览用槽底而非第二张纸面；axe（默认规则）在空态 / 任务 / 文件 / 事件 / 计划 / Trace / 设置七种状态下 0 违规。
- 2026-10-27（评审返工）：模型控件保留原生 `<select>`（F4 票面允许「原生 select 做 chip」，不自绘浮层，仅收窄为 chip 形态）；事件页按载荷去重并限 100 条；运行中的计划保留禁用「编辑」按钮并用原因行说明（`aria-describedby`）；抽屉关闭时用 `inert` + `aria-hidden` 退出 Tab 序；≤860 标题给浮动按钮让位；≤1400 路径收窄、中文标签回归 Inter、新样式不再使用 `space-y`。
- 2026-10-30（#265，按 dsh 形态，owner 实施前明确要求）：对话去掉「第 N 轮」分段标签与 `RoundDivider`；用户气泡后的过程改为 `TurnProcess` 折叠行，展开只含本轮步骤（后端新增 `StepRecord.round` 与 `step_start.round`）；回答从「唯一纸面 + provenance 头部」改为平铺正文；`round=0` 旧记录收进「历史执行过程」块。
- 2026-10-30（#266，按 dsh 形态）：文件预览从列表底部移到第三栏页签；每个打开文件一个页签，同一路径复用，关闭回到相邻页签（优先左侧）/ 文件列表；预览失败按 415 / 400 / 404 / 409 分行并给下一步，截断明示，下载入口始终保留。
- 2026-10-30（#265 评审返工）：计划提示按轮追加（plan 模式下第 2 轮的回答不再写进第 1 轮的槽位），本轮没有新提示时也不再删旧提示；「历史执行过程」块移到对话最前；计划入口行移出轮次块、固定在全部轮次之后；`TurnProcess` 与 `StepRow` 共用 `DisclosureHead`，压缩标记的轮次归属在 `Conversation` 一次算清（`lib/markers`）。
- 2026-10-31（#266 评审返工）：页签 chip（`div[role=tab]`）补回 `touch-action: manipulation`；打开文件后焦点落到新页签、关闭后焦点回到相邻页签（`focusTab` 取代拿私有 DOM id）；方向键在页签内嵌关闭按钮上同样移动焦点；隐藏页签不再即时预取；404 的 `task not found` / `file not found` 词汇写入 `architecture.md` §3.3；下载入口抽成 `FileDownloadLink`。
- 2026-10-31（#265 二次返工）：构建模式回答先 strip 再入账（尾随换行不再让本轮回答从对话里消失）；计划提示追加时同样过 redact；无主压缩标记不再塞进最前的「历史执行过程」，改为对话末尾中性的「上下文压缩记录」；抽出 `CompressMarker`。
- 2026-11-07（P0-4 / #282）：前端测试栈拍板——本批不引 jsdom / @testing-library（D-23），改「纯函数 / store 断言 + 手测 checklist」纪律（D-24，见 §10.1）；历史计划「组件不做渲染测试」的声明加取代注。
- 2026-11-07（P1-1 / #257）：窄屏抽屉断点收敛到 `frontend/src/lib/breakpoints.ts` 单一来源（`(width < 860px)` / `(width < 1180px)`，与 CSS `max-[860px]` / `max-[1180px]` 同界，860 / 1180 恰好仍是宽屏，取代 859.98 / 1179.98 写法）；`drawerView` 派生遮罩 / 背景 inert / 变宽与 Trace 的关闭信号；`useDrawerFocus` 负责触发元素记录、焦点移入、Esc / Tab 圈定、关闭先撤 inert 再归还焦点（inert 由 `lib/inertLedger` 记账撤销）；§6 增窄屏抽屉手测清单。
- 2026-11-07（P1-2 / #258）：长内容断行契约收敛到 `lib/longContent`（`LONG_TEXT`，用于 Markdown 根 / 用户气泡 / 失败块 / 工具入参出参，静态源码守卫防回退）；工具长入参「展开全部 / 收起」（`lib/toolInput`，800 code point 阈值）；事件账本 heartbeat 不进用户面（`lib/eventLedger.isLedgerType` 排除 + `ledgerEvents` 去重 / 100 上限 / 倒序；store `applyEvent` 早退且只替换真正变化的 slice；Trace 保留原始流）；请求 / 渲染去重（选中已停下任务 parked 快照只拉一次、PlanPane 初载不自触发、`task_created` 回放不 bump `listRevision`、Markdown `memo` + 结构守卫）；Composer / FilePane 错误文案带原因与下一步、文件面板「重试」、状态行 `role=status`；§6 增 P1-2 手测清单。
- 2026-11-07（P2-1 / #232）：Composer 拆分——`lib/composerState` 纯函数 `composerView`（状态矩阵与逐字文案，入参含 `sendError` 以保住 `bottomNote` 纯函数）与 `clearedDraft`；`hooks/useComposerSend` 收口 text / busy / sendError 与 `createTask` / `sendMessage`（返回同一份 `view` 供组件渲染，避免双算），`hooks/useComposerControls` 收口 draftMode / draftModel / switchNote 与 `putMode` / `putTaskModel` + `getTask`；`Composer.tsx` 276 → 164 行，只留结构、Enter 处理与 reset effect；`BACKEND_UNREACHABLE` 抽 `lib/copy`；行为与逐字文案不变，§6 增拆分手测清单。
- 2026-11-07（P2-3 / #243，D-37）：设置页 Provider 化——`store/modelStore` 改为「Provider 卡片（`GET /providers`）+ 扁平模型条目（`GET /models` 兼容投影，一次请求覆盖全部条目 id）」双读；`components/settings` 拆为 `SettingsDialog`（卡片列表编排）+ `ProviderCard`（卡片事实 / 设为默认 / 编辑 / 删除二次确认 / 模型目录 / 发现预览）+ `ProviderDialog`（新建两页签：第三方预设 / 自定义，编辑 id 只读、Key 只写、清除粘贴 Key 二次确认）；`lib/modelCatalog` 收敛分组、默认动作（全局默认是条目级，卡上「设为默认」取该 Provider 首条目）、表单校验、发现勾选与失败文案（原因 + 下一步）；Composer 模型控件改原生 `<select>` + `<optgroup>`，新增「目录外模型…」行（现有任务走 `PUT /tasks/{id}/model` 的 `{provider, model}` 自动建 manual 条目；新建任务因创建接口只收条目 id，先 `POST /providers/{id}/models` 再创建），旧 `Task.model` 条目 id 用回退 option 兼容显示；旧 `/api/models` 消费点保留（不再有扁平设置表单的写路径，投影读仍是条目映射的唯一读取）；§6 增 P2-3 手测清单（后续 P2-4b 改为只读 `GET /providers` + `GET /providers/{id}/models`，见下条）。
- 2026-11-07（P2-3 / #243，rebase 到 #298 合并后）：协议文案按已合并事实更新——Anthropic 原生（非流式）创建 / 编辑可用（不再有「保存 400 / 后端 PR B」说法）；提示明示两项剩余限制：模型目录发现仅 OpenAI 兼容支持、thinking / reasoning_effort 对 Anthropic 暂不支持；§6 对应手测项与纯函数断言同步（`PROTOCOL_OPTIONS` 的 hint 不得回退到旧 400 文案）。
- 2026-11-08（P2-4b / #267，D-38 / D-40）：思考展示与参数入口——`StepRecord.reasoning` 可选字段（D-38 契约，字段暂缺时兼容）；`StepRow` 展开区新增默认折叠的「思考」块（空 / 缺省不渲染，长文本断行 + 限高滚动，行头标「思考」），`TurnProcess` / `AnswerSheet` 不变；reducer 把 `final_answer.reasoning` 挂到最后一步（快照仍权威）；`lib/modelParams` 收敛参数表单的 tri-state 映射、区间校验（temperature 按协议 / max_tokens 正整数）、显式 null 清空与「只提交改动字段」，设置页模型行新增「参数」表单（条目级；Provider 默认以继承提示展示；Provider 级默认编辑留后续片 / 参数入口二期）；`store/modelStore` 模型目录改读 `GET /providers/{id}/models` 原始条目（D-36 需要 raw 覆盖值，legacy 投影只有生效值）；§6 增 P2-4 手测清单。
- 2026-11-08（P4-2 / #67）：设置页新增「能力」区——预设三选一（minimal / research / full）+ 每包三态 override（跟随预设 / 强制开 / 强制关）；「当前生效」面展示能力包与工具清单（来自 `GET /api/plugins` 的 `active`，前端不自行推导）；**文件存在时** `saved` 与 `active` 不同才提示「重启后台后生效」（无文件时 `restart_required` 恒 false，当前生效面来自默认 / 启动参数）；保存中禁用预设与三态并显示「保存中…」、错误横幅「重试」可重试、服务端 4xx / 422 原因原样显示；`lib/plugins.ts` 纯函数（三态 ↔ PUT body、dirty 判定、active 摘要）与 `store/pluginsStore.ts`（加载 / 保存 / 失败保留旧快照 / in-flight 守卫）覆盖机械面；§6 增 P4-2 手测清单（含 D-24 接线项与保存中错误态）。
- 2026-11-09（#287 修复）：最终回答步骤的行首摘要不再重复回答正文——`lib/stepSummary` 把 `step.thought`（trim 后）与本轮任一 `AnswerSheet` 回答相等时收敛为「最终回答」；`Conversation` 按轮把 answers 传给 `TurnProcess` → `StepRow`，历史任务同规则生效；§6 P2-4 清单补对应手测项。
- 2026-11-09（#287 返工 / PR #317 评审 F2）：`TurnProcess` 轮次结论从「任一步失败即处理失败」改为 `lib/rounds.ts::turnVerdict` 的「末步定结论」——崩溃后续跑的轮次保留失败断点步（红点行），但续跑产出回答后轮次头显示「已完成」而不是永久「处理失败」；`live` 仍让运行中的轮次显示「执行中」（末步已失败除外）；旧数据里「末步 done、前面残留 running」按执行中兜底（启动收尾前的记录才会出现）；§6 增续跑轮次头手测清单。
- 2026-11-09（#287 retro / 回放幂等）：`store/reduceTask.ts` 的 `tool_call` 回放不再一律追加到末步——先按 call id 在全部步骤中定位归属，已知调用只更新原步骤且保留快照里的终态（不把收尾步骤翻回 `running`）；快照从未见过的调用才落到当前步，末步已收尾时同样不翻状态；`tool_result` 只更新仍 `pending` 的调用，回放帧不覆盖快照终态；`tool_result` 与 `tool_call` 共用同一归属查找。修 P5-1 真机复验发现的「重开已停下任务时末步工具数翻倍、轮次头显示执行中」；§6 增回放手测清单。
- 2026-11-18（#328 S1）：旧 `/api/models` 扁平投影与前端兼容读取删除（ADR 0013 决策 1）；设置页只读 `GET /providers` + `/providers/{id}/models`；`lib/modelCatalog` 删除 `entriesFromProfiles`，`api/client` 删除 legacy CRUD 函数；`ModelProfile` / `ModelsData` 类型退役。
- 2026-11-18（#329 S2）：旧任务格式整文件拒绝、`step.round` 必填；前端删除 round=0 旧轮次展示与无步骤 `tool_call` 桩；§6 增 S2 手测清单。
- 2026-11-18（#334 S8）：Trace 区新增模型调用列表/按需详情（筛选 `type=llm_call` + `call_id`，读请求/响应/错误/usage，`truncated` / 未完成标记；完整导出仍走无参 `/trace`）；`lib/modelCalls` 按 `(call_id, attempt)` 去重；`store/reduceTask` 的合并改为显式 `(round, step, plan_version)`，快照/事件/续说迟到响应不再用数组长度判新旧；终端只加一行模型调用摘要 + `/trace` 指针；§6 增 S8 手测清单。
- 2026-11-18（#335 S9）：文件面板由固定 2s `setInterval` 改为请求结束后再排下一次（`lib/filePolling` 单飞循环，stop 清定时器并用代次丢弃在途结果，同时 `AbortController` 取消浏览器请求）；隐藏 / 切任务 / 重试不再让旧响应落地，失败仍按节奏重排并保留手动重试与终态刷新；不宣称取消浏览器请求等于停止服务端扫描；§6 增 S9 手测清单。
- 2026-11-20（#343）：Changes 面——round 尾部卡（默认 4 行、hover 500ms 限高 420px 摘要、点击开页签）与按 `(task, round)` 唯一的 review 页签（文件选择、reviewed 内存标记、合并/并排、换行、打开/下载、binary/oversized 状态）；`lib/changes` 收敛卡模型、flat/split 行、状态与失败文案，`store/changesReviewStore` 负责会话内 reviewed（切任务清空）；后端新增只读 changes API 与 sidecar，顶部固定说明「不是完整工作区 diff」；不做 apply/reject。
- 2026-11-20（#341 Files 树）：文件面从按目录分组平铺改为可折叠工作区文件树（D1 布局 A，不做硬分栏）；`lib/fileTree` 收敛树构建 / 自然排序 / 可见行展开 / 过滤（命中分支连祖先一起展开，不改用户展开集合）/ 键盘转移；`FilePane` 接 roving tabindex + `role=tree`、轮询保留展开与滚动、任务切换重置，打开预览时自动展开其祖先；`fileGroup`（平铺分组）退役；§4 FilePane 规则与 §6 手测清单同步。
- 2026-11-20（#344）：文件预览升级为只读代码视图——面包屑（深路径折叠、420px 不溢出）+ 大小 + 下载；独立行号 gutter（`aria-hidden` / `select-none`，不进复制文本）与等宽代码两列，长行只在正文内横向滚动、gutter sticky、`tab-size: 2`；按扩展名用 Shiki 细粒度按需加载（`shiki/core` + JS regex engine + 逐语言动态 `import`，无 Monaco / 无第二编辑器），主题走 `createCssVariablesTheme`，颜色映射到现有 app token（不新增颜色 token；关键字复用行内代码的信号蓝）；最多渲染 2000 行（`MAX_RENDER_LINES`），后端 200KB 与行数上限分别提示并可在两限同时命中时合成一句；空文件空态；415 / 400 / 404 / 409 / 500 / 断网保留可区分文案与下载。机械面 `lib/codePreview.test.ts`，§6 增 #344 手测清单。
- 2026-11-21（#346 浮动树）：Files 预览 header 增加树 toggle，复用 #341 树与 #344 预览（单轮询 + 共享展开/筛选）；`lib/navigatorLayout` 固定行宽 <400 或视口 <768 铺满、400–559 浮动（默认 260 / 最小 180 / 最大 `min(300,row-120)`）、≥560 贴边（最大 `min(300,row-320)`，body ≥320）；拖拽与 ←/→、Home/End 调整宽度，`lib/navigatorState` 按 task 记忆开合与宽度；打开焦点进入首个可见行，Esc 关闭回 toggle，float/fill 覆盖时预览正文 inert；文件树滚动位置跨预览 tab/开合恢复。§4 增 `FileNavigator` 规则，§6 增 #346 手测清单。
- 2026-11-25（#353）：设置页新增内置 OpenCode Go 模板（只预填 Provider ID / 名称 / 地址 / 协议 / 环境变量名，不预置模型、不写存储；同 id 已存在时隐藏）；新建 Provider 对话框的自定义页签新增草稿模型目录——用当前地址 / 协议 / 环境变量 / Key 调 `POST /providers/discover`，候选可搜索 / 勾选 / 全选 / 手动添加，保存时随 Provider 原子创建；关闭 / 切页签不写状态。`lib/modelCatalog` 收敛模板填充、默认勾选、过滤、全选与保存 payload 纯函数；§4 `SettingsDialog` 行与 §6 P2-3 手测清单同步。
