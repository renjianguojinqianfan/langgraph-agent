# Workspace 文件树、预览与变更 UI（#341–#346）执行 spec

> 状态：2026-11-20 执行 spec。覆盖 #341、#342、#343、#344、#345、#346。
> 各票 AC 是执行合同；本文件补**跨票术语、布局目标与 #343 的数据/API/安全契约**，不重复每张票的验收清单。
> 研究依据：DeepSeek Harness `2e39e70`（`ui-sidebar-files`、`ui-deliverables`、`workspace-changes`、`ui-input-trigger`）、
> VS Code `1.147.0`、Codex / Cline / OpenHands / opencode；详细证据见公开仓库与上述 commit。

## 1. 三面模型

| 面 | 票 | 职责 |
|---|---|---|
| **Files** | #341 树、#344 预览、#346 浮动树 | 浏览任务工作区的全部文件；只读；树 + 代码预览 |
| **Changes** | #343 | 审阅**每个 round** 由 `write`/`edit` 产生的净变更；本轮尾部卡 + review tab |
| **Input** | #342 命令、#345 技能 | `/` 命令菜单与技能唤起；不做 `@file` |

420px 右栏：Files 先做 A（树 tab + 预览 tab），目标 C 是 #346 的浮动树；Changes 卡在**本轮对话尾部**，review tab 在右栏。**任何一面都不做硬分栏。**

## 2. 术语

- **round**：1-based 对话轮次，沿用 `round_no` / `StepRecord.round`。本仓库**不使用 dsh 的 turn 作为领域词**；票面/API/UI 统一写 round。
- **change**：某个 task 在某个 round 内，由 `write`/`edit` 触达的文件的净变更。不是 git diff，也不是工作区全局 diff。
- **reviewed**：前端会话内的“已看过”标记，不持久化、不上传、不改变文件。
- **Files / Changes**：两个独立面；Files 是浏览，Changes 是审阅，不能合成一个树。

## 3. 票面地图与 blocking

| 票 | 交付 | Blocked by |
|---|---|---|
| #341 | Files 树（A：树 tab） | None |
| #344 | 代码预览（只读、Shiki） | None（可与 #341 并行） |
| #346 | Files 浮动树（C：400–560px 树浮在预览上） | #341、#344 |
| #343 | Changes 卡 + review tab + capture/API | None |
| #342 | `/` 菜单 + 内置命令 | None |
| #345 | 技能目录 + `/` 唤起（Phase 1） | #342 |

## 4. Files 面

- **#341 树**：根展开、其余折叠；自动展开当前文件祖先；2s 轮询保留展开/选中/滚动；过滤按名称/路径；空目录不显示；2000 条截断；第一片不做单子目录链压缩。A 片不做硬分栏。
- **#344 预览**：点文件开预览 tab；面包屑、行号、Shiki 按需高亮、长行横向滚动、200KB/2000 行上限、binary/415/404/500 回退；只读。代码/纯文本优先；Markdown/图片/PDF/Office 另开票。
- **#346 浮动树**：420px 落在 dsh 的 400–560px 带：树默认 260px、最小 180px、浮在预览上，预览保持整宽；<400px 填满面板；≥560px 侧栏并排；<768px 视口整栏。树状态按 task/pane 记忆，Escape 关闭，焦点回到 toggle。

## 5. Changes 契约（#343）

### 5.1 捕获

- 只捕获 **`write` / `edit` 成功执行的工具调用**；blocked/failed 不记录。
- 每个 `(task_id, round, path)` 在**本轮第一次成功触碰前**保存 `before`（若之前不存在则标记 added），每次成功变更后更新 `after`；round 结束（停下/出错/完成）时 finalize。
- 纯 shell 写入不列；被 `write`/`edit` 触达的路径，round 末的 `after` 可能包含其后 shell 改动——UI/文档要说明「变更集不是完整工作区 diff」。
- 当前文件工具没有 delete/rename：**deleted 状态保留在 API/类型中，但当前不会产生**；不要把它写成已支持。
- 同一文件在本轮多次写入合并成一张净变更；文件在 round 中途被 shell 删除且后续无 after 时，按 `deleted` 保留（仅当已有 before）或在实现时明确降级为 `missing`；不允许静默丢记录。
- **归属**：记在触发该工具调用的 Task 上；子代理若以独立 Task 运行则各自记录，父任务不合并；两个任务共享工作区时，各自记录自己的 before/after，不承诺全局一致。

### 5.2 存储与生命周期

- 新增 `CHANGES_DIR`（Settings，默认 `<DATA_DIR>/changes`）；测试隔离必须重定向，会话守卫覆盖 `changes`。
- 结构：`<CHANGES_DIR>/<task_id>/<round>/<index>/{meta.json,before,after}`；`index` 按 `display` 排序后的稳定位置；`meta.json` 存 path/display/status/added/deleted/binary/oversized/coarse/sizes/tool_call_id。
- 文本内容存 sidecar；**不进 `tasks.json`、trace、SSE、日志**。任务删除时删除该 task 的目录；启动对账清理不属于任何任务的孤儿目录。
- 重启后仍可读；round 结束前中断也保留已捕获部分；resume 开启新 round，不覆盖旧 round。
- 上限：`maxFiles=500`、`maxFileBytes=2 MiB`、`maxRoundBytes=64 MiB`、`maxRenderedLines=2000`、`diffTimeout=100ms`。超过 maxFiles：`total` 报完整数、`files` 前 500、`truncated=true`；超过单文件/整轮字节：该文件标 `oversized`，不存内容、不计算 diff；diff 超时：单 hunk 整段替换并标 `coarse`。
- binary：任一侧含 NUL 或不是合法 UTF-8 → `binary:true`，不存内容，不计算 diff。
- 安全：sidecar 可能含文件内容/密钥；默认在本地 `data/`，API 只对已认证本地客户端开放；不返回绝对路径；任务删除清理；按仓库规则做一次 `security-review`。

### 5.3 API（只读）

- `GET /api/tasks/{id}/changes?round=N`
  - 200：`{round, total, truncated, added, deleted, files:[{index,path,display,status,added,deleted,binary,oversized}]}`
  - 无变更：200 + `total:0, files:[]`；任务不存在 404；round 非法 400；认证同其他 task 路由。
- `GET /api/tasks/{id}/changes/{round}/{index}`
  - 200：`{path,status,added,deleted,binary,oversized,coarse,truncated,hunks:[{oldStart,oldLines,newStart,newLines,lines:[...]}]}`
  - binary/oversized：带 flags、`hunks:[]`；index 不存在 404。
- `path` 为 task 相对 POSIX 路径；不返回 `CHANGES_DIR` 或本机绝对路径。
- 不新增写端点、不新增下载端点；文件下载沿用现有 `/files/download`。

### 5.4 UI

- **卡**：每个 round 的本轮对话尾部一张（最终回答之后、下一个用户输入之前）；无变更不显示；header（`已编辑 N 个文件` 或单文件）+ 文件行；默认 4 行后折叠；hover 500ms 出 unified 预览（最大 420px）；行/头点击打开 review tab。
- **review tab**：按 `(task_id, round)` 一个；已存在则复用/切换，不重复开；文件选择器、unified 默认 + split 切换（单侧变更自动 unified）、wrap、打开文件（复用 #344 预览 tab）/下载；`reviewed` 每文件一个 checkbox + `N/M reviewed`；不做 apply/reject。
- **reviewed**：前端内存态，按 round/task 重置；不持久化、不上传。
- 420px 单列；binary/oversized 行有状态 + 下载；不把完整 diff 硬塞进卡。
- 视觉沿用应用 token；不做全屏 review（全屏/加宽另开票）。

### 5.5 测试

- 后端：write/edit 捕获、first-touch before、round 末 after、added/modified、纯 shell 不列、failed/blocked 不记录、binary/oversized/coarse、maxFiles/maxFileBytes/maxRoundBytes、任务删除清理、孤儿清理、重启可读、并发任务、API auth/404/400/空、路径不泄漏。
- 前端：卡分组/折叠/计数、review tab 生命周期、reviewed 重置、unified/split、大文件/二进制、任务切换；纯函数/store 断言 + 手测清单，不引 jsdom。
- 门禁：后端 ruff/mypy/离线套件；前端 typecheck/test/build；文档链接/机器路径/设计 token 按触发面。

## 6. Input 面

- **#342**：`/` 在开头/空白后/标点后触发；URL/路径不触发；命令集 `/new`、`/stop`、`/plan`、`/build`、`/model`、`/resume`、`/tasks`、`/help`；无匹配按普通文本发送；执行命令清 token 不发消息；不做后端。
- **#345**：`GET /api/skills`（task_id/workspace、尊重 `skills` 包、只回 name/description/layer、不回路径）；技能组；选中插入可见引用；Phase 1 靠现有 `load_skill`，真机 Trace 必须证明调用；Phase 2 强制注入另开票。
- **同名技能**：`load_skill` 会把 home 与 workspace 两层的同名 `SKILL.md` 都返回；菜单按 name 去重并标注「home + workspace」，选择后的可见引用只写 name（加载两层），不要暗示只选了一层。

## 7. 验收矩阵（跨票）

| 行为 | 票 | 证据 |
|---|---|---|
| 嵌套树展开/过滤/轮询保状态 | #341 | 树纯函数/状态测试 + 手测 |
| 代码预览行号/高亮/截断/回退 | #344 | 预览纯函数测试 + 手测 |
| 420px 浮动树 | #346 | 手测（400/420/560/<400/<768） |
| write/edit 后卡与 review tab | #343 | 后端捕获/API 测试 + 前端 store 断言 + 手测 |
| `/` 命令 | #342 | 触发/命令纯函数测试 + 手测 |
| 技能目录与真机 load_skill | #345 | API 测试 + 真机 Trace |
| 文档/安全/门禁 | 全部 | 各票 AC |

## 8. 不做

- 编辑/保存/重命名/删除/上传；git Changes/diff；apply/reject；shell 写入捕获；@file（本轮）；Markdown/图片/PDF/Office 预览；Monaco；硬分栏；全屏 review；服务端枚举/忽略语义改动。

## 9. 研究依据（公开）

- DeepSeek Harness `2e39e70`：`packages/client/ui-sidebar-files`、`ui-sidebar-documentpreview`、`ui-deliverables`、`ui-input-trigger`、`packages/deliverables/workspace-changes`。
- VS Code `1.147.0`：Explorer 压缩树、preview/pinned tab、diff ≤900px inline、SCM 默认 list、agent 编辑三层 Keep/Undo。
- Codex / Cline / OpenHands / opencode：changed-files 列表、unified 窄栏、全量逃生口、只读审阅、状态持久化。
- 详细 `path:line` 与 permalink 见公开仓库与上述 commit；会话调研记录不随仓库发布。
