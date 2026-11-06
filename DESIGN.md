---
name: "LangGraph 自主任务 Agent（qianfan）"
description: "一条 loop、计划/执行两模式、停下续说的本地自用 agent；记录优先的安静监控台"
colors:
  page-ink: "#0A0A0B"
  column-ink: "#131316"
  sheet-ink: "#17171B"
  well-ink: "#121215"
  hover-ink: "#202027"
  text-primary: "#E8E8EA"
  text-secondary: "#A6ABB2"
  text-tertiary: "#8B9097"
  rule: "rgba(255, 255, 255, 0.07)"
  rule-strong: "rgba(255, 255, 255, 0.12)"
  signal-now: "#4C8DFF"
  text-on-signal: "#0A0A0B"
  state-ok: "#3FB97F"
  state-warn: "#D9A441"
  state-danger: "#E5534B"
typography:
  title:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15.5px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.85
  label:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.6
  machine:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tabular-nums"
  code:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.7
  invite:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 500
    lineHeight: 1.35
rounded:
  control: "6px"
  well: "8px"
  sheet: "11px"
  composer: "12px"
  dialog: "14px"
components:
  button-primary:
    backgroundColor: "{colors.signal-now}"
    textColor: "{colors.text-on-signal}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-quiet:
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-outline:
    textColor: "{colors.text-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-danger:
    textColor: "{colors.state-danger}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  composer-input:
    backgroundColor: "{colors.well-ink}"
    textColor: "{colors.text-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.composer}"
    padding: "10px 12px"
  model-select:
    backgroundColor: "{colors.well-ink}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
    height: "26px"
  tab-active:
    textColor: "{colors.text-primary}"
    typography: "{typography.label}"
    padding: "0 10px"
    height: "36px"
  task-row-selected:
    backgroundColor: "{colors.well-ink}"
    textColor: "{colors.text-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
  dialog:
    backgroundColor: "{colors.column-ink}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.dialog}"
    padding: "0"
    width: "min(760px, calc(100vw - 2rem))"
  well:
    backgroundColor: "{colors.well-ink}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.code}"
    rounded: "{rounded.well}"
    padding: "12px"
---

# Design System: LangGraph 自主任务 Agent（qianfan）

## Overview

**Creative North Star: "安静的监控台"**

界面是一台不抢戏的记录仪：左边是任务的历史，中间是正在发生的对话，右边是工作区的文件、计划与事件。它不表演「AI 很忙」，只让人看清这一刻发生了什么、需要人做什么。设计上的野心全部花在精确与克制上：机器事实用等宽字体对齐，人话用 Inter 说清楚，颜色只在必须区分状态时出现。

深浅只有三级明度（页 → 栏 → 槽），分隔靠 1px 发丝线，没有阴影、渐变、辉光或装饰边框。动效只做状态变化的确认（150ms 的颜色与 0.96 的按压缩放），不做氛围动画；抽屉用 220ms 的 `cubic-bezier(.32,.72,0,1)` 滑动，`prefers-reduced-motion` 下全部停用。

**Key Characteristics:**

- 三栏记录结构：264 / 流式（内容测度 760）/ 420；行、条、平铺正文三种对象形状。
- 近黑炭底 + 低饱和灰阶；一个信号蓝只标「此刻 / 可操作」，三个状态色只用于状态点与状态词。
- Inter 负责人话与标题，JetBrains Mono 负责机器事实（路径、工具名、计数、时间），数字一律 `tabular-nums`。
- 扁平、发丝线分层、零阴影；选中态用墨色 + 2px 内嵌脊线，不用信号蓝。
- 状态与计划版本永远来自任务快照（REST）；SSE 只给节奏，不做假进度、不反写状态。

## Colors

近黑炭底上的低饱和灰阶承担全部结构，一个信号蓝承担「此刻」，三个状态色承担结果。

### Primary

- **信号蓝（Signal Now）** (#4C8DFF): 全局唯一强调色。只允许出现在五处：运行状态点、发送键底、`:focus-visible` 焦点环、链接与行内代码、当前文件。它标记「此刻正在发生」或「现在可以操作」，不是选中色。

### Neutral

- **页底炭黑（Page Ink）** (#0A0A0B): 全局背景，也是信号蓝底上的字形颜色。
- **栏面（Column Ink）** (#131316): 左右栏、任务头、页签条；比页底亮一级。
- **纸面（Sheet Ink）** (#17171B): 设置对话框内的槽底、链接；#265 起回答改为平铺，内容区不再有纸面。
- **槽底（Well Ink）** (#121215): 代码块、工具入参出参、输入框、分段控件底、选中任务行。
- **悬停底（Hover Ink）** (#202027): 行与按钮的 hover、用户气泡底。
- **主文字（Primary Text）** (#E8E8EA): 标题、回答正文、用户记录。
- **次文字（Secondary Text）** (#A6ABB2): 正文与步骤描述、次级按钮。
- **三文字（Tertiary Text）** (#8B9097): 机器事实、时间、计数、placeholder。
- **发丝线（Hairline Rule）** (rgba(255,255,255,.07)): 行与栏的分隔线，只做分隔。
- **控件环（Rule Strong）** (rgba(255,255,255,.12)): 按钮 / 输入框的描边、滚动条拇指。

### Semantic

- **成功（State OK）** (#3FB97F): 只用于工具调用成功与产物 / 成功标记。
- **停下 / 警告（State Warn）** (#D9A441): 任务「已停下」状态点与状态词。
- **失败 / 危险（State Danger）** (#E5534B): 任务失败状态，以及删除类按钮的描边与字形（`/40` 描边、`/10` 悬停底）。

### Named Rules

**The One Signal Rule.** 信号蓝只出现在五处（运行点、发送键、焦点环、链接与行内代码、当前文件）。选中态一律用墨色 + 2px 内嵌脊线，信号蓝不参与选中；扩张这五处必须先改这条规则。

**The No-Chrome Rule.** 无渐变、无辉光、无网格、无装饰边框；发丝线只做分隔，不做装饰。

**The State-Color Rule.** 状态色只做状态点与状态词，不做大块底色；`--state-ok` 不用于任务状态（任务状态用信号蓝 / 警告 / 危险 / 三文字）。

## Typography

**Display Font:** Inter Variable（fallback: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif）
**Body Font:** Inter Variable（同上）
**Label/Mono Font:** JetBrains Mono（fallback: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace）

**Character:** 一个人话，一个机器事实。Inter 的 13px 正文在深色底上保持长时间阅读的舒适；JetBrains Mono 只出现在机器拥有的事实上，把「人写的」和「系统记录的」在视觉上分开，不需要额外标签。

### Hierarchy

- **Title** (600, 15.5px/1.4, -0.01em): 任务标题、回答标题、对话框标题；页面唯一 `h1`。
- **Body** (400, 13px/1.85): 对话、回答、计划、输入框正文。
- **Label** (400–500, 12px/1.6): 标签、按钮、说明、相对时间。
- **Machine** (400, 11px/1.5, `tabular-nums`): 路径、工具名、计数、时长、时间、ID。
- **Code** (400, 12px/1.7): 代码块、文件预览、工具入参出参。
- **Invite** (500, 21px/1.35): 未选任务时的邀请句；全系统唯一大于 15.5px 的字号。

### Named Rules

**The Mono-Is-Meaning Rule.** 等宽字体是语义不是纹理：只写机器拥有的事实。中文标签、状态词、标题永远用 Inter。

**The Tabular-Numbers Rule.** 用于比较的数字一律 `tabular-nums`，且不截断。

**The One-H1 Rule.** 页面唯一 `h1` 是任务标题；回答标题从 `h2` 起；计划文档从 `h2` 起、小节用 `h3`。

## Layout

三栏固定：左栏 264（新任务、搜索、目录上下文、任务历史、工作区切换与设置入口）、中栏流式（内容测度 760；任务头 52 → 子智能体条（有才出现）→ 对话（唯一滚动区）→ 常驻 composer）、右栏 420（文件 / 计划 / 事件页签 + 文件预览页签）。

- **对齐**：全部左对齐；唯一右对齐的是用户气泡，以及单元格内需要比较的数字。
- **断点**：<1400 路径芯片收窄；<1180 右栏变抽屉；<860 左栏变抽屉（CSS `max-*` 不含边界值，JS 侧对齐 1179.98px / 859.98px）。
- **抽屉**：220ms `cubic-bezier(.32,.72,0,1)`；关闭时 `inert` + `aria-hidden` 退出 Tab 序；全屏布局补 `env(safe-area-inset-*)`。
- **滚动**：对话区与抽屉 / 对话框 `overscroll-behavior: contain`；细滚动条 8px。
- **节奏**：4 / 8 / 12 / 16px 的间距阶梯（p-1 / p-2 / p-3 / p-4）；页签高 36、控件高 26–32、按钮高 28 或 32；行与页签无圆角。

**The Three-Shapes Rule.** 三种对象三种形状：记录 = 行（无卡、无圆角）、子智能体 = 条（压在对话上方）、回答 = 平铺正文（不套纸面）。

## Elevation & Depth

**没有阴影。** 深度只由三级明度与 1px 发丝线表达：页 (#0A0A0B) → 栏 (#131316) → 槽 (#121215 / #17171B)。光从上方来、只上一级；没有渐变、辉光、网格或装饰边框。对话框是独立一级（`rounded-dialog`，14px），靠 `backdrop:bg-surface-page/80` 的遮罩与明度区分，不靠阴影。

### Named Rules

**The Flat-by-Default Rule.** 表面默认是平的；`box-shadow` 只允许出现在两处：分段控件与选中态的 2px 内嵌脊线（`inset`），以及 `active:scale-[.96]` 的按压反馈。新增外阴影必须先改这条规则。

## Shapes

形状语言是「表格与记录」：行和页签是 0 圆角，只有可点的控件有 6px 圆角（`rounded-control`），槽底 8px（`rounded-well`），纸面 11px（`rounded-sheet`），composer 12px（`rounded-composer`），对话框 14px（`rounded-dialog`），胶囊 999 仅保留在语义确实是标签/计数的小块上。选中态用 2px 内嵌脊线（分段控件与页签在底边，左栏任务行在左侧），不用背景色块；描边一律 1px 发丝线或 `rule-strong`。

## Components

### Buttons

- **Shape:** 6px 圆角（`rounded-control`），高 28（`sm`）或 32（`md`）；`active:scale-[.96]`，150ms 过渡。
- **Primary:** 信号蓝底 + `text-on-signal` 字形（对比度 6.18:1），用于发送键等「此刻可操作」的动作；hover 为 `signal-now/90`。
- **Quiet:** 无底，次文字色；hover 换 `surface-hover` 底、主文字色。图标按钮同形态（32×32，`aria-label` 必填）。
- **Outline:** 1px `rule-strong` 描边 + 主文字色；hover 换悬停底。
- **Danger:** `state-danger/40` 描边 + 危险色字形；hover 换 `state-danger/10` 底。删除类动作才用它。
- **Focus:** 全局 `:focus-visible` 2px 信号环、offset 2px；禁用态 `opacity-50`，且光标不是手型。

### Tabs / Navigation

- **Shape:** 高 36、无圆角；激活态主文字色 + 底边 2px 主色脊线（`after:inset-x-1.5`），未激活三文字色、hover 次文字色。
- **Behavior:** `role=tablist/tab/tabpanel` + `aria-controls`；←/→/Home/End 移动并聚焦；文件页签内嵌关闭按钮（24×24），关闭后焦点回到相邻页签（优先左侧）。

### Composer / Inputs

- **Style:** 槽底 `well-ink`、1px `rule-strong` 描边、12px 圆角、10×12 内边距、13px/1.85 正文；placeholder 用三文字色。
- **Focus:** 描边换 `signal-now`（不发光、不加外阴影）。
- **Composer:** 模式分段控件 + 模型原生 `<select>`（chip 形态，高 26）+ 输入 + 发送键；运行中禁用并说明原因；Enter 发送 / Shift+Enter 换行。

### Chips / Segmented

- **Style:** 槽底容器 + 2px 内边距、6px 圆角；选项无底，选中态主文字色 + 底边 2px 主色内阴影，未选中三文字色。
- **State:** 用 `aria-pressed`，不做高亮背景；点击目标高 ≥24。

### Cards / Containers

- **Rows:** 任务行、步骤行、工具行都不做卡片：行形状、0 圆角；步骤行左边 gutter 放状态点，任务行把状态词 + 相对时间放在标题下一行。hover 用悬停底；选中行只换主文字色 + 左侧 2px 内嵌脊线，不铺底色。
- **Wells:** 工具入参出参、代码块、文件预览用槽底 + 8px 圆角 + 12px 内边距；代码 12px/1.7 mono。
- **Dialog:** 原生 `<dialog>`，`min(760px, calc(100vw - 2rem))` 宽、`rounded-dialog`、栏面底、`backdrop:bg-surface-page/80`；焦点圈定、Esc 关闭、归还焦点。

### Status

- **Style:** 色点 + 状态词双通道（不只靠颜色）；`role=status` + `aria-live=polite`。
- **Semantics:** 启动中 = 三文字色；运行中 = 信号蓝；已停下 = 警告色；失败 = 危险色；状态只能由任务快照决定。

## Do's and Don'ts

### Do:

- **Do** 用记录与行组织信息：状态词 + 色点同时出现，数字用 `tabular-nums`。
- **Do** 把机器事实交给 mono，人话交给 Inter；一个信号蓝只标「此刻 / 可操作」。
- **Do** 禁用时说明原因，错误说清「原因 + 下一步」；空态说明这里为什么空。
- **Do** 让回答正文直接承担成品；每轮的过程收进 `TurnProcess` 折叠行。
- **Do** 用 1px 发丝线与三级明度表达层次；选中态用 2px 内嵌脊线。

### Don't:

- **Don't** 用中圆点串元信息、用信号蓝做选中色、把一切做成同圆角同阴影的卡片。
- **Don't** 在自由 Markdown 上猜进度、造动画或假进度条；不做「看起来忙」的表演。
- **Don't** 用 SSE 回放改写任务状态；状态与计划版本只认任务快照（REST）。
- **Don't** 加渐变、辉光、外阴影、装饰边框或大圆角卡片；这些是这套系统的反参考。
- **Don't** 在内容区引入第二张「纸面」；#265 起回答是平铺正文，只有设置对话框内部有纸面。
