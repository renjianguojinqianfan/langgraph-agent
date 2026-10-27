# 前端设计基线（Frontend Design）

> **状态**：设计权威，**已交付**（2026-10-23 创建；2026-10-27 按 [`docs/specs/frontend-redesign-plan.md`](specs/frontend-redesign-plan.md) 的 F1–F9 + B1 落地；#189 已由 F1 修复）。现役代码按本基线交付，视觉与交互以本文为准。
> **权威顺序**：功能范围 → #54（含 2026-10-19 / 2026-10-23 校准段）；视觉与交互规则 → 本文；接口契约 → `architecture.md` §3.3 / §3.4（SSE 词汇表有 `npm run check:sse` 守卫）。
> 本文只写规则与判据：实现步骤写进 PR，需求原文留在 #54，不在此复制。

## 0. 方向与三个承诺

**安静的监控台**：一本能说话的飞行记录仪。左列任务、中列对话、右列工作区；子智能体是一条压在对话上方的飞行条。

1. **记录是唯一权威**：状态、计划版本、文件读数一律来自任务快照（REST）；SSE 只提供节奏，事件回放不得改写快照（#189）。
2. **机器事实用 mono，人话用 Inter**；一个信号蓝只标「此刻 / 可操作」。
3. **三种对象三种形状**：记录 = 行、子智能体 = 条、回答 = 内容区唯一纸面（对话框是覆盖层，不算纸面）。

## 1. Token

| 语义 | 变量 | 值 | 白名单 |
|---|---|---|---|
| 页底 | `--surface-page` | `#0A0A0B` | 全局底 |
| 列面 | `--surface-column` | `#131316` | 左右栏、header、页签条 |
| 纸面 | `--surface-sheet` | `#17171B` | **内容区只有**回答卡（对话框是覆盖层，不算纸面） |
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
| 左栏 264 | 新任务、搜索、目录上下文（本目录 / 全部）、任务历史（分组、状态点 + 词、相对时间）、设置入口 | 任务详情、文件内容 |
| 中栏 | 任务 header（标题 / 模式词 / 目录 / 状态 / 停止 / 右栏开关）、子智能体条、对话记录、计划入口行、回答卡、composer | 常驻文件树、第二个输入框 |
| 右栏 420 | 页签：文件 / 计划 / 事件（事件页底部「打开完整 Trace」→ 中栏全宽回放面，复用现役 `TraceTab`）；文件预览与计划编辑就地展开 | 子智能体页签（子智能体只在顶部）、卡片套件 |

- 子智能体条：**只在顶部**、可折叠、只在任务含子智能体时出现；点卡片在条下就地展开详情。只有执行模式可能有子智能体（计划模式的工具面没有 `spawn_subagent`）。
- 对齐：全部左对齐；唯一右对齐的是用户气泡，以及单元格内需要比较的数字。
- 断点：≤1400 路径芯片收窄；≤1180 右栏变抽屉（220ms `cubic-bezier(.32,.72,0,1)`）；≤860 左栏变抽屉。全屏布局补 `env(safe-area-inset-*)`。
- 滚动：对话区与抽屉/对话框 `overscroll-behavior: contain`。

## 4. 组件与状态

| 组件 | 规则 |
|---|---|
| `TaskHeader` | 标题（15.5、单行截断、`title`）；第二行「模式词 + 工作目录」（mono、截断、`title`）；右侧状态、停止、右栏开关。模型只在 composer。 |
| `TaskRail` | 左栏：搜索、目录上下文（本目录 / 全部）、任务行（状态点 + 词 + 相对时间）、设置入口；行截断 + `title`。 |
| `Status` | 单一来源：启动中 / 运行中 / 已停下 / 失败；色点 + 文字双通道；`role=status aria-live=polite`。 |
| `SubagentStrip` / `Plate` | 平板条（无阴影、发丝分栏）；每张 = 名字 + 状态词（Inter）+ 机器事实（短 id + 产物数，mono；无 token / 耗时）；名字截断 + `title`；点开就地展开详情（摘要 / 产物 / 边界说明）。 |
| `StepRow` | 行形状（无卡、无圆角）；左 gutter 放状态点；主体 = 一句话 + 右列机器事实；点开原地展开工具行。 |
| `ToolRow` | 槽底；工具名与参数 mono、可截断 + `title`；结果词只用**终态**三值：成功 / 失败 / 未执行（`success / failed / skipped`；`pending` 是进行中，不改结果词。原因写进 `error`；不新增 `blocked` 状态）。 |
| `PlanAnchor` | 对话里一行入口：「计划 v3 · 状态」（版本 = `Task.plan_version`；状态词由 `Task.status` 派生，不是计划自己的字段）；点开切右栏计划页；不复制全文。 |
| `AnswerSheet` | **内容区唯一纸面**（11 圆角、唯一阴影）；头部只放 provenance：模型 = `Task.model` 的档案名（空则全局默认），工具次数 = `steps[].tool_calls` 全任务累计（写「累计」，不是本轮），不放「回答」标签；正文 Markdown（标题 / 列表 / 代码 / 行内代码）。 |
| `Composer` | 模式段 + 模型 chip + 输入 + 发送（蓝底 + `--text-on-signal` 字形，6.18:1；不用白字 3.20:1）；Enter 发送、Shift+Enter 换行，提示只出现一处；运行中禁用并说明原因。 |
| `FilePane` | 平铺清单由客户端按 `/` 分组；文本预览 ≤200KB；二进制给下载；>2000 条明示截断。 |
| `PlanPane` | 渲染 Markdown + 标题目录；版本 / 状态徽标；**编辑仅「已停下」可用；失败待后端放宽（见 §9）**，未放宽前禁用并写明原因；保存提示「下一条消息生效」。 |
| `EventPane` | 时间 mono + 类型词 + 摘要；不做可点假象；注明事件只做旁证、状态以快照为准。 |
| `TraceFace` | 保留现役 `TraceTab` 能力（`GET /trace`，NDJSON 回放 / 导出）；入口在右栏事件页底部，打开时中栏临时全宽显示并带返回；本期不重构。 |
| `SettingsDialog` | 原生 `<dialog>`（焦点圈定 / Esc / 归还焦点）；模型 CRUD 与 Key 状态；删除 / 清除 Key 二次确认。 |

### 现役组件 → 目标组件（实施对照）

| 现役 | 目标 | 处理 |
|---|---|---|
| `TaskView` | 三栏骨架 | 重排 |
| `HistoryPanel` | `TaskRail`（左栏） | 重做（目录分组、相对时间） |
| `TaskPanel` | 中栏（对话 + composer）+ `SubagentStrip` | 拆分 |
| `TaskHeader` | `TaskHeader` | 重做（两行信息、非终态停止） |
| `InputBar` | `Composer` | 并入（模式 / 模型 / 发送） |
| `MessageStream` | 对话记录（气泡） | 重做（轮次、Markdown） |
| `StepTimeline` + `StepDetail` | `StepRow` 行内展开 | 合并，右栏不再放步骤 |
| `PlanPanel` | `PlanPane` + `PlanAnchor` | 拆成右栏页签 + 对话入口 |
| `WorkspaceFiles` | `FilePane`（含预览） | 保留能力，重排 |
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
| 运行中 | `--signal-now` | 禁用 | 显示 | — |
| 已停下 | `--state-warn` | 可用 | 不显示 | 续说 |
| 失败 | `--state-danger` | 可用（B1 已放宽，见 §9） | 不显示 | 续说 |
| 未选任务 | 无 | 可用 | 不显示 | 新建 |

## 5. 交互规则

- **计划**：对话一行入口 + 右栏计划页；展示 Markdown 与标题目录；**不做打勾清单**（无状态源）。编辑仅在停下 / 失败（B1 已放宽 `PUT /plan`，见 §9）；运行中点编辑给出原因而不是藏按钮。保存 = 整份 PUT；提示「下一条消息生效」；发现外部改动提示「载入新版 / 覆盖保存」。可选非阻断动作「切到执行并继续」= 切模式 + 聚焦输入框，不发送。
- **模式 / 模型**：都在 composer（下一轮生效）；任务头只读显示模式词。不引入确认闸门（ADR 0008）。
- **停止 / 续说**：停止在任务头；不设独立「继续」按钮——发下一条消息即续说；placeholder 与状态词承接语义。
- **文件**：不承诺目录懒加载、mtime、新旧标记（后端没有）；只承诺平铺清单 + 文本预览 + 下载 + 截断提示。
- **历史**：默认当前目录；「全部」按目录分组、行上标目录名；行显示状态词 + 相对时间；交接提示本期不做（无后台身份数据）。
- **多轮**：对话按「第 N 轮」分段；用户记录用浅底气泡；历史轮的步骤不可精确回溯（数据无轮次归属），不承诺。
- **快捷键**：Enter 发送 / Shift+Enter 换行；提示只出现一处。

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

## 7. 空 / 错 / 加载

- 空（未选任务）：一句 21px 邀请 + 一行副说明；composer 可用。
- 空（列表 / 文件 / 计划 / 事件）：说明「这里为什么空」+ 下一步动作；不渲染破 UI。
- 错：说原因 + 下一步（失败可续说；或新建任务重来）；不道歉、不模糊。
- 加载：占位文案以「…」结尾；不用假进度（计划不打勾、任务不显示假进度条）。

## 8. Do / Don't

**Do**：用记录与行组织信息；把机器事实交给 mono；让状态文字与色点同时出现；禁用时解释原因；让唯一纸面承担成品。

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

## 11. 指针（先读哪份）

- 需求与范围：#54（含 2026-10-23 校准段）。
- 运行时与契约：[architecture.md](architecture.md) §3.3（REST）/ §3.4（SSE）/ §3.5（错误口径）。
- 模式与状态机：[ADR 0008](adr/0008-single-loop-plan-build-modes.md)、日常形态 [ADR 0010](adr/0010-launch-dir-workspace-and-model-profiles.md)。
- 网页验收：[manual-verification.md](manual-verification.md)（9 场景）。
- 原型：历史存档在分支 `prototype/frontend-redesign`（交互语义已过期，仅视觉参考）；本轮会话稿不入库。**实现以本文与 #54 为准**。

## 12. 变更记录

- 2026-10-23：创建。来自 #54 设计 grilling；原型经六个前端设计 skill（frontend-design / web-design-guidelines / accessibility / break-ui / emil-design-eng / performance）打磨后沉淀。
- 2026-10-23：按 PR #192 评审修订：`PUT /plan` 放宽列入 §9、provenance 来源点名、Trace 归宿（`TraceFace`）、状态改为「未交付」+ 现役→目标组件映射、发送键对比度（深色字形）、ToolRow 三值、状态色点列、纸面口径（对话框不算纸面）、#54 旧 token 作废、字体/Markdown 依赖登记、原型指针改指存档分支。
- 2026-10-23：按 PR #192 第二轮复审修订：PlanPane 只给「已停下」编辑（失败待 §9）、SubagentStrip 状态词归 Inter、机器事实改短 id + 产物数、AA 旧值依据纠正（`#63676D` 不足、`#9BA0A6` 为统一色阶）、`--text-on-signal` token、`TaskRail` 组件规则、`--state-ok` 用途收窄、ToolRow 改「终态三值」、依赖措辞改「兜底（非等价替代）+ 实施 PR 补记录」、AnswerSheet 补「空则全局默认」。
- 2026-10-27：按 [`frontend-redesign-plan.md`](specs/frontend-redesign-plan.md) 分片定稿：实施指针从「#54 四期」改为 F1–F9 + B1；依赖状态更新为已批；`--radius-control: 6` 待 F2 补入 §1；D1–D11 实现裁决见计划 §4.2。
- 2026-10-27：**已交付**（F1–F9 + B1 全部合入）。实际落地与本文的差异只在实现细节：计划页编辑含失败态（B1）、事件页时间用到达时间（SSE 帧不带 ts）、文件预览用槽底而非第二张纸面；axe（默认规则）在空态 / 任务 / 文件 / 事件 / 计划 / Trace / 设置七种状态下 0 违规。
