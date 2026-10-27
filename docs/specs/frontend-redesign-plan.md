# #54 前端重设计「安静的监控台」：分片实施计划

> **状态：已交付**（2026-10-27 落库；开工基线 `a6bcfe7`，仅在 `15d64cf` 之上补了一篇 `docs/retros/`，代码未动；F1–F9 + B1 全部合入，PR 见 #228）。
> 设计权威是 [`docs/frontend-design.md`](../frontend-design.md)（目标态，交付前标「未交付」）；需求与范围权威是 #54（含 2026-10-23 校准段与本文件 §9 的 2026-10-26 审计段）。
> 本文只管**切片、判据、风险与文档面**，不重复设计理由。实施按 [`docs/agents/workflow.md`](../agents/workflow.md)：qoder 实施 + `code-review` 自评审 + `neat-freak` 收尾 + PR；opencode 独立评审；评审侧 / 用户合并。
> 单集成分支交付（workflow.md「并行实施」2026-10-28 起的 `implement-spec` 形态）；每片在独立 worktree 分支完成后合入集成分支，最后统一评审、提一个 PR。

## 1. 一页结论

- **按区域竖切**：每片 = 一个区域的视觉 + 接线一次做完；合完界面可用、可单独回滚。现役功能面已接好线，重写主要是搬家。
- **顺序**：

```
F0 文档与票 → F1 #189 + vitest 接缝 → B1 失败可续说/改计划 → F2 设计地基
            → F3 左栏 → F4 任务头 + Composer → F5 对话记录
            → F6 右栏三页签 → F7 子智能体条 → F8 设置对话框 → F9 收尾
```

- B1 与前端各片无代码冲突，放在 F4 之前，让 Composer 一次上「失败可续说」，不留过渡禁用态。
- F2 是 F3–F8 的地基；F2 之前只有 F1、B1 可并行。
- **测试接缝 = vitest，只测纯逻辑**（reducer、相对时间、字节数、分组），组件不做渲染测试。
- **UI 层零新增依赖**：只借 beautiful-ui 的形态与交互规则，不装组件包；组件 props 留 `clsx`/`cn` 升级缝。
- **本文件是活文档**：未交付增量的判据可以随实施校准；已交付部分不改写，现状写进 `README.md` / `docs/architecture.md`。

## 2. 现状证据

行号锚在 `a6bcfe7`（`15d64cf` 只加了 `docs/retros/`，代码未动）。只列本计划要动的面。

| 面 | 现状 | 证据 |
|---|---|---|
| 布局 | 三栏硬拼：`HistoryPanel` + `TaskPanel`（中栏含页签：运行 / 子任务 / Trace）+ `StepDetail`（右栏）；无 264 / 760 / 420 栅格，无断点抽屉 | `frontend/src/pages/TaskView.tsx:78-84`、`frontend/src/components/TaskPanel.tsx:17-92` |
| 视觉 token | 仅 `--color-panel` / `--color-panelMuted` 两个自定义色；其余全用 slate / sky 默认调色板；无 `color-scheme: dark`、无 focus-visible 环、无 reduced-motion 基线 | `frontend/src/index.css:9-12` |
| 字体 | 系统栈，无 Inter / JetBrains Mono；机器事实（路径、工具名、时间）与正文同字体 | `frontend/src/index.css:21-26` |
| 状态展示 | `TaskHeader` / `HistoryPanel` / `SubtaskList` 三处各抄一份状态色与词；PENDING 显示「等待中」；停止按钮只在 RUNNING 显示 | `frontend/src/components/TaskHeader.tsx:6-18,55`、`frontend/src/components/HistoryPanel.tsx`、`frontend/src/components/SubtaskList.tsx` |
| #189 | SSE 回放 `task_created(PENDING)` 整体覆盖 GET 快照；首轮后续事件没有把状态改回 RUNNING 的路径 | `frontend/src/store/taskStore.ts:65-73,132-201`；复现与根因见 #189 |
| store 结构 | `applyToTask` 与 store 写在同一文件、不可单测；`upsertTask` 是整体替换 | `frontend/src/store/taskStore.ts:64-130,153-163` |
| 输入 | `InputBar` 内嵌 mode / model / textarea / 发送；仅 `Ctrl/⌘+Enter` 发送，无 IME 判断；失败任务输入框禁用 | `frontend/src/components/InputBar.tsx:34-40,159-176` |
| 对话记录 | `MessageStream` 纯文本 + `StepTimeline` 行；无轮次分段、无 Markdown、无用户气泡 | `frontend/src/components/MessageStream.tsx`、`frontend/src/components/StepTimeline.tsx` |
| 右栏 | `StepDetail` 占右栏；`WorkspaceFiles` 与 `PlanPanel` 挤在中栏滚动流里；`TraceTab` 是中栏页签 | `frontend/src/components/TaskPanel.tsx:63-90` |
| 设置 / 登录 | `SettingsPage` 420 行整页覆盖，无原生 `<dialog>`、无焦点圈定；`LoginPage` 用 slate / sky 默认色 | `frontend/src/pages/SettingsPage.tsx`、`frontend/src/pages/LoginPage.tsx` |
| CI | frontend job = `npm ci → typecheck → check:sse → build`，无 `npm test` | `.github/workflows/ci.yml:150-185` |
| 后端续说 / 改计划 | `_continue` 与 `set_plan` 只接受 `INTERRUPTED`；FAILED 需先改后端 | `backend/services/task_manager.py:1190-1221,1223-1282` |
| 计划读取 | `GET /plan` 只回 `{text, version}`，不含 hash；外部改动只能靠保存前重取比对 | `backend/services/task_manager.py:1183-1188` |
| 文件端点 | 任务作用域的 `GET /tasks/{id}/files`、`/files/preview`、`/files/download` 已交付（ADR 0010 切片），不是 #54 原文的 `/api/files` | `backend/api/routes.py`；ADR 0010 |
| 步骤数据 | `Task.steps` 跨轮累积，`StepRecord.index` 累计编号，无轮次字段 | `backend/services/task_manager.py:1328` 注释「step_index stays cumulative」 |

## 3. 目标形态与差异

| 类别 | 内容 |
|---|---|
| **保留** | 一条 loop、两模式、状态三值、SSE 事件词汇表、任务作用域文件 / 计划接口、现有 store 的 action 面（`upsertTask` / `applyEvent` / `setTasks` / `historyFilter` / `settingsOpen`） |
| **重排** | 三栏 264 / 流式（内容测度 760）/ 420；中栏 header 52 → 子智能体条（有才出现）→ 对话（唯一滚动区）→ composer（常驻） |
| **新增** | `ui/*` 共用原子（Icon / Button / Status / Segmented / Tabs）；左栏 `rail/*`；中栏 `center/*`；右栏 `dash/*`；设置对话框；vitest 纯逻辑测试；三栏断点抽屉 |
| **替换** | `HistoryPanel → TaskRail`；`TaskHeader` / `InputBar` / `ModeSwitch` / `ModelSelect → TaskHeader + Composer`；`MessageStream` / `StepTimeline → Conversation + StepRow`；`WorkspaceFiles` / `PlanPanel` / `StepDetail → DashTabs + FilePane / PlanPane / EventPane / TraceFace`；`SubtaskList → SubagentStrip / Plate`；`SettingsPage → SettingsDialog` |
| **不动** | SSE 事件词汇表（`docs/architecture.md` §3.4 + `hook/useSSE.ts` + `types/index.ts` + `check:sse` 四处）；`TraceTab` 内部；登录流程；后端文件 / 计划 / 模型接口的契约（B1 只放宽 messages / plan 两个端点的状态门） |
| **可动** | `store/taskStore.ts`：抽 reducer、#189 状态收敛、F7 的 subtask 实时态；事件词汇表不动 |

## 4. 切片

### 4.1 通用门禁（每片）

- `npm ci → npm run typecheck → npm test → npm run check:sse（动事件面时）→ npm run build`；后端片另跑 `pytest backend/tests/ -q`、`ruff check backend scripts`、`mypy`。
- 受影响区域 1920 / 1440 走查（playwright-core + 本机 Chrome；browser-use 视口太小不能判布局）。
- 触到的组件过设计基线 §6 a11y 清单；键盘全流程。
- 新样式显式写 `leading-*`、不依赖 `space-y`（Tailwind v4 位移，见 #54 2026-10-14 评论）。
- `code-review` + `pr` + `neat-freak`；opencode 独立评；用户合并。
- 视觉验收做元素级对照，不做整图像素 diff。

### 4.2 实现裁决（D1–D11，按此实施，不再逐项问）

| # | 裁决 |
|---|---|
| D1 | 选中态用墨色 + 2px 内嵌脊线（任务行 / 文件行左侧；页签 / 模式底部）；不用填充底、不用信号蓝。走查觉得难读再改基线，不在实现里偷偷用填充。 |
| D2 | 文件行选中 = 墨色脊线；预览头部的当前路径 = 信号蓝（唯一一处）。 |
| D3 | 增加 `--radius-control: 6`；F2 同步补进设计 §1（补全，不改既有值）。 |
| D4 | F2 补 ≤1400 路径芯片收窄、`env(safe-area-inset-*)`、右抽屉 220ms `cubic-bezier(.32,.72,0,1)`。 |
| D5 | 模式词 Inter；只有目录路径用 mono（mono 只写机器事实）。 |
| D6 | ToolRow 的 `pending` 显示「进行中」，不算结果词；结果词只保留成功 / 失败 / 未执行。 |
| D7 | PlanAnchor 状态词由 `Task.status` 派生，不读计划字段。 |
| D8 | Markdown 标题层级：回答、计划都从 h2 起（h3 小节）；页面唯一 h1 在 TaskHeader。 |
| D9 | 计数 / 时间 / 路径事实 `tabular-nums`；用于比较的数字不截断。 |
| D10 | 设置对话框 `overscroll-behavior: contain`；Esc / 焦点归还按 §6。 |
| D11 | 登出入口移到左栏页脚（`RailFooter`）；任务头只留状态 / 停止 / 右栏开关。 |

### 4.3 F1 #189 + 测试接缝

- 内容：抽出纯 reducer（`store/reduceTask.ts` 的 `applyToTask`）；`task_created` 只在 store 里还没有该任务快照时写状态；收到 `step_start` 且状态仍是 PENDING 时重取一次任务快照（按任务去重，不循环），**只合并状态字段、不整体替换 detail**（整体替换会用持久化快照里旧 steps / 旧 conversation 冲掉 SSE 实时数据）；停止按钮在 PENDING/RUNNING 显示、PENDING 词改「启动中」（只动 `TaskHeader.tsx:14,55`；历史列表的「等待中」留给 F2 的 `Status` 统一）；装 `vitest ^5.0.5`，`npm test`，CI 在 typecheck 后加一步（`.github/workflows/ci.yml:171-172` 之间）。
- 替换 / 涉及：`taskStore`、`TaskView`、`TaskHeader`、`package.json`、`ci.yml`、`README:19/97/101`、`AGENTS:39/66`。
- 判据：回归测试在旧代码上红、新代码绿（「RUNNING 快照 + 回放 task_created(PENDING) → 仍 RUNNING」；「无快照 + task_created(PENDING) → PENDING」；「合并状态不清空 steps」）；手验 SSE 重连后头部不变「等待中」、停止按钮在；`npm run check:sse` 绿；PR 正文记录选定修法（B + step_start 重取快照）；`Closes #189`。
- 随片文档：README、AGENTS、CI 注释、`frontend-design.md:3` 的 #189 状态。

### 4.4 B1 失败可续说 / 改计划

- 内容：`POST /messages` 与 `PUT /plan` 接受 FAILED（`task_manager.py:1203,1242`）；**resume 仍只收 INTERRUPTED**；续说时清空 `task.error`（`task_manager.py:1262` 置 RUNNING 处一并清）；无 checkpoint 的 FAILED 仍 409；前端 `parked` 判定（`InputBar.tsx:35`、`PlanPanel.tsx:13`）与文案放开——这处前端改动是**一次性过渡**（两文件 F4 / F6 会整体替换），只为让 B1 能在网页端做真模型手验。
- 替换 / 涉及：后端 + 测试（改 `test_s5_agent_modes.py:423-439` 参数表去掉 FAILED；新增 messages 的 FAILED 有 checkpoint / 无 checkpoint / RUNNING 三例；`test_resume.py:288-303` 的 FAILED 409 保持）、`architecture.md:142`（plan PUT 行「仅停下时」）+ `:140`（messages 行）、ADR 0008 决策 5 行内修订注、`frontend-design.md` §4 矩阵 / §9 第 1 项。
- 判据：TDD 先红；后端全门禁；**前端 typecheck / test / build 也要绿**（改了 InputBar、PlanPanel 两行）；真模型手验：坏 Key 档案跑出 FAILED → 换好档案 → 续说成功；结果按 `manual-verification.md` 表格式追加，证据落 `docs/assets/verification/<日期>/`。
- 随片文档：`frontend-design.md` §4 状态矩阵 / §9-1、`architecture.md:140/:142`、ADR 0008 决策 5 行内注。

### 4.5 F2 地基

- 内容：`index.css` `@theme` 写入 §1 全部 token（**补 `--radius-control: 6`**）、`color-scheme: dark`、`:focus-visible` 信号环、reduced-motion 基线；**旧 token `--color-panel` / `--color-panelMuted` 先按新值保留**（HistoryPanel / StepDetail / PlanPanel / TraceTab / LoginPage / SettingsPage 还在用 `bg-panel*`，消费者迁完再删）；装两套字体；内联 SVG 图标组件；`Status` 单一来源（四态色点 + 词，`aria-live`）替换 TaskHeader / HistoryPanel 私有表；TaskView 改三栏骨架 264 / 流式 / 420 + 断点（≤1400 路径芯片收窄、≤1180 右栏抽屉 220ms `cubic-bezier(.32,.72,0,1)`、≤860 左栏抽屉、`env(safe-area-inset-*)`），现役组件先原样放进新栏位。
- 替换 / 涉及：`index.css`、`TaskView`、新 `components/ui/*`。
- 判据：三栏两档宽度无溢出；旧组件背景不丢；中间态「新底色 + 旧组件样式」属预期。
- 随片文档：`frontend-design.md` §1（补 `--radius-control`）/§2 字体。

### 4.6 F3 左栏 TaskRail

- 内容：新建 / 搜索（前端过滤已载列表）/ 本目录·全部（复用 `historyFilter`）/「全部」按目录分组 + 行上目录名 / 状态点 + 词 / 相对时间 `time[datetime]` / 设置入口 / 登出入口；截断 + `title`。
- 替换：`HistoryPanel`。
- 判据：分组、相对时间纯函数有 vitest；键盘可达。
- 随片文档：无（设计基线已覆盖）。

### 4.7 F4 任务头 + Composer

- 内容：`TaskHeader`：h1 标题单行截断 + 第二行「模式词（Inter）+ 目录（mono）」+ Status + 停止（非终态）+ 右栏开关；`Composer`：模式段 + 模型 chip + 输入 + 发送（蓝底深字）；**Enter 发送 / Shift+Enter 换行，输入法组字中（`isComposing`）的 Enter 不发送**；按状态矩阵禁用并写原因。
- 替换：`TaskHeader`、`InputBar`、`ModeSwitch`、`ModelSelect`。
- 判据：状态矩阵五行逐条走查；中文输入法回车不误发。
- 随片文档：无（设计基线已覆盖）。

### 4.8 F5 对话记录

- 内容：按「第 N 轮」分段（用户回合起轮，`conversation` 里 user / assistant 成对，assistant 回合在停下时才落库）；用户浅底右对齐气泡；`StepRow` 行内展开 `ToolRow`（合并 `StepTimeline` + `StepDetail` 的步骤部分，结果词终态三值）；**步骤整段渲染在当前活动区（最后一条用户记录之后），不按轮拆**——`Task.steps` 跨轮累积、`StepRecord.index` 累计编号，没有轮次字段，多轮任务的这个块里会含早先轮的步骤，按设计 §5「历史轮步骤不可精确回溯，不承诺」处理；`AnswerSheet` 唯一纸面 + provenance（模型档案名 / 累计工具次数）+ `react-markdown`（不开原始 HTML）；压缩标记行内；失败块。
- 替换：`MessageStream`、`StepTimeline`；`StepDetail` 暂缩成事件日志。
- 判据：Markdown 不执行 HTML；长回答 / 长工具名 / 空回答不破版；多轮任务步骤块不冒充「本轮」。
- 随片文档：`frontend-design.md` §2 Markdown。

### 4.9 F6 右栏三页签

- 内容：`tablist` 方向键；`FilePane`（按 `/` 分组、就地预览 ≤200KB、二进制给下载、>2000 明示截断）；`PlanPane`（Markdown + 标题目录、仅停下（B1 后含失败）可编辑、运行中给原因、保存提示「下一条消息生效」）；**外部改动检测用「保存前重取一次文本、与加载时内容比对」**（`GET /plan` 只回 `{text, version}`、不含 hash；比对不同 → 提示「载入新版 / 覆盖保存」，不需后端改动）；`EventPane`（时间 mono + 类型 + 摘要，注明只做旁证；事件只存在内存、刷新后为空，空态指向 `TraceFace` 回放）；底部「打开完整 Trace」→ 中栏全宽 `TraceFace`（复用 `TraceTab`，带返回）；对话里 `PlanAnchor` 一行（版本取 `Task.plan_version`）。
- 替换：`WorkspaceFiles`、`PlanPanel`、`StepDetail` 剩余部分；`TaskPanel` 去掉 Trace 页签。
- 判据：三页签键盘全流程；编辑中外部改文件、保存时提示出现；刷新后事件页给空态不装死。
- 随片文档：无（设计基线已覆盖）。

### 4.10 F7 子智能体条

- 内容：中栏顶部可折叠条，仅有子任务时出现；卡 = 名字 + 状态词 + 短 id + 产物数；点开就地展开（摘要 / 产物 / 边界说明）；reducer 接 `subtask_start/result/failed` 做实时态（不动事件词汇表）。
- 替换：`SubtaskList`；`TaskPanel` 页签条移除。
- 判据：reducer 新分支有 vitest；计划模式无条。
- 随片文档：无（设计基线已覆盖）。

### 4.11 F8 设置对话框

- 内容：`SettingsPage` → 原生 `<dialog>`（焦点圈定 / Esc / 归还焦点 / `overscroll-behavior: contain`），模型 CRUD + Key 状态，删除 / 清 Key 二次确认；`LoginPage` 只做 token 适配。
- 替换：`SettingsPage`、`LoginPage` 样式。
- 判据：键盘开关对话框、焦点归还。
- 随片文档：无（设计基线已覆盖）。

### 4.12 F9 收尾

- 内容：§6 a11y 全清单、§7 空错加载、reduced-motion、1920/1440 全走查；`manual-verification.md` 场景 2/3/6/7 网页端重跑并按结果表追加行（证据落 `docs/assets/verification/<日期>/`）；#189 回归再走一遍；README 截图更新；`frontend-design.md:3` 状态改「已交付」+ §12 changelog；AGENTS:39「未交付」→「已交付」；清理本地草稿（`.agents/temp/grill/`、`.qoder/tmp/preview-v9/`，删前先问）；`Closes #54`。
- 判据：四种状态（通过 / 跳过 / 未运行 / 产物验收）分开报；场景 2/3/6/7 全有结果或写明跳过原因。

## 5. 文档面（neat-freak 步 3.5）

| 片 | 随片文档 |
|---|---|
| F1 | README、AGENTS、CI 注释、`frontend-design.md:3` 的 #189 状态 |
| F2 | `frontend-design.md` §1（补 `--radius-control`）/§2 字体 |
| F5 | `frontend-design.md` §2 Markdown |
| B1 | `frontend-design.md` §4 状态矩阵 / §9-1、`architecture.md:140/:142`、ADR 0008 决策 5 行内注 |
| F9 | `frontend-design.md` §3 状态「已交付」、§12 changelog、AGENTS:39「未交付」→「已交付」、README 截图 |

票面只在 F0 与 F9 动。`docs/agents/domain.md` 的 specs 树与 `frontend-design.md` 指针在 F0 同步。

## 6. 验收

- 机械：`npm ci → typecheck → test → check:sse（动事件面时）→ build`；后端片加 `pytest` / `ruff` / `mypy`。
- 走查：1920 与 1440 两档；键盘完成主要操作；`prefers-reduced-motion` 下无循环与位移。
- #189 回归：运行中触发 SSE 重连，头部不出现「等待中」、停止按钮不消失。
- 真模型：`manual-verification.md` 中 UI 相关项（2 / 3 / 6 / 7）网页端重跑并追加结果；无法运行时写明「未运行」与原因。
- 设计基线自检项见 `frontend-design.md` §6 / §10。

## 7. 风险

- 中文输入法回车误发（F4 必测 `isComposing`）。
- F2–F8 之间 master 视觉新旧混搭（竖切的代价，已接受）。
- 视觉面无机械守卫：只靠走查截图对照（元素级，不做整图像素 diff）。
- B1：LLM 出错导致的 FAILED，最后一轮 assistant 回合不在历史里；推理型供应商只在停下路径验证过 → B1 真模型手验覆盖。
- 步骤无轮次归属（跨轮累积、编号连续），F5 只能整段渲染；按轮归属需后端加字段，本章不做。
- F1 的运行时快照只能用「只合并状态」的方式回填，整体 replace 会清掉 SSE 实时 steps / conversation（现役 `upsertTask` 是整体替换，停下时的重取没错，运行中不能复用）。
- SSE 重连每次回放 `task_created` 都会撞 `listRevision` → 历史列表多发一次 GET；只多请求、不改状态，不在 F1 处理。
- vitest peer 已核实（2026-10-26：`vitest@5.0.5` 支持 `vite ^6.4 / ^7 / ^8`；CI Node 22 满足 `@types/node` peer），无剩余阻塞。
- worktree 下 `frontend/node_modules` 可能是 junction / 缺失，跑门禁前先 `ls -ld`。

## 8. 不做（本章）

命令面板（v9 有）、千级列表虚拟化与分页、计划 / 模式 / 模型变更事件（多标签页会脏，已知）、文件 mtime / 目录懒加载 / 新增标记、任务列表摘要投影、子智能体 token / 耗时、单后台交接提示、主题切换、Trace 重构、登录页重设计、URL 状态同步、计划页「切到执行并继续」可选动作（设计 §5 标可选）。其中设计 §9 的 2–7 项本章不做到，需要时另开票；其余为显式排除。

## 9. GitHub 动作与一致性审计

### 9.1 #54 / #189 处理（2026-10-26 审计）

| # | 位置 | 现状（互相矛盾） | 处理 |
|---|---|---|---|
| A1 | #54 :164 vs :195 | 「保留：四栏布局」 vs 同日晚「三栏 = 左任务历史 / 中对话 / 右工作区」 | 三栏为准；原文 :69 的四栏 DOM 顺序作废（原文保留） |
| A2 | #54 :168-173 vs :236 | 分期「待拍板」四期 vs 备注「仍按四期执行」 | 四期横切作废；改为 F1–F9 + B1 竖切 |
| A3 | #54 :91 / :166 vs 设计定居 #189 段 | 「Zustand store、SSE 处理一行不动」 vs #189 必须修在 store | store 可动（#189、reducer 提取、F7 的 subtask 实时态）；SSE 事件词汇表不动 |
| A4 | #54 :36 / :101-105 vs ADR 0010 | 「后端仅新增两个只读文件端点」 vs `GET /tasks/{id}/files`、`/files/preview`、`/files/download` 已交付 | §6 作废，引用现役任务作用域端点；本章不新增文件端点 |
| A5 | #54 :130 vs 本轮 | 「本 spec 不引入前端测试框架」 vs 引入 vitest | 改为：纯逻辑用 vitest，组件不做渲染测试 |
| A6 | #54 :114 vs 本轮 | `react-markdown` + `remark-gfm`「待批」 vs 已批 | 追加段记录批准清单（Inter、Markdown 两包、vitest；UI 零新增） |
| A7 | #54 :181 vs B1 | 「本章不动后端 API」 vs 放宽 messages / plan 到 FAILED | 唯一例外：这两个端点 + `architecture.md:140/:142` + ADR 0008 注；SSE 面不动 |
| A8 | #54 标题 vs 设计定居 | 标题「四栏方向」 vs 三栏 | 标题改「三栏」；原文不删 |
| A9 | #54 :177 vs F1 | 「#189 修复或明确记录为已知问题」 vs 本轮选修复 | 选修复；F1 `Closes #189`，F9 再走查 |
| A10 | #189「候选修法（未定）」 | 票面仍未记录选定方案 | F1 PR 正文记录「B + step_start 重取快照」与红→绿证据 |
| A11 | `frontend-design.md:3` | 「实施按 #54 四期；#189 仍 open」 | F0 改为指向本文件；#189 状态在 F1 后更新 |
| A12 | `frontend-design.md:54` | Markdown 依赖「仍待批」 | F0 更新为已批（与 A6 同步） |
| A13 | `frontend-design.md:123` | 可选动作「切到执行并继续」 | 本章不做，写进 §8「不做」 |
| A14 | `domain.md:59` | 文件树写「实施见 #54 四期」 | 改为本文件；`:51` specs 树补一行 |
| A15 | README :19/:97/:101、AGENTS :39/:66、ci :12/:171-172 | 门禁清单不含 `npm test` | F1 同步加 `npm test`（CI 在 typecheck 后） |

### 9.2 子票

F1–F9 + B1 共 10 张，均 `ready-for-agent`，`Part of #54`，每张只写范围 + 判据 + 指向本文对应小节。完成判据 = 集成分支合入 + PR `Closes` 票号。

### 9.3 依赖批准（2026-10-26）

`@fontsource-variable/inter`、`@fontsource/jetbrains-mono`、`react-markdown` + `remark-gfm`、`vitest ^5.0.5`。UI 层零新增依赖（只借 beautiful-ui 形态，不装组件包）。

### 9.4 默认值

1. B1 只放开续说与改计划，**resume 不放开**（失败后原样续跑会把同一轮再跑一遍、大概率撞同一个错）。
2. B1 续说时清空 `task.error`。
3. F1 直接 `Closes #189`，F9 再走查一遍。
4. #54 加 `spec` 标签，F1–F9、B1 开子票（共 10 张）。
5. 不新开 ADR：前端在设计基线内；B1 在 ADR 0008 决策 5 加行内修订注（先例：同文决策 7 的 C1 修订注）。
